// End-to-end test through librime's public C API, including schema deployment.
#include <rime_api.h>
#include <cstring>
#include <iostream>
#include <stdexcept>
#include <string>
#include <vector>

namespace {
RimeApi* api;
RimeSessionId session;
bool deploy_failed = false;

void require(bool condition, const std::string& message) {
  if (!condition) throw std::runtime_error(message);
}

void notification(void*, RimeSessionId, const char* type, const char* value) {
  if (std::strcmp(type, "deploy") == 0 && std::strcmp(value, "failure") == 0)
    deploy_failed = true;
}

std::vector<std::string> compose(const std::string& keys) {
  api->clear_composition(session);
  for (unsigned char key : keys)
    require(api->process_key(session, key, 0), "Key not handled: " + std::string(1, key));
  RIME_STRUCT(RimeContext, context);
  require(api->get_context(session, &context), "No Rime context");
  const bool has_preedit = context.composition.length > 0 && context.composition.preedit;
  std::vector<std::string> result;
  for (int i = 0; i < context.menu.num_candidates; ++i)
    result.emplace_back(context.menu.candidates[i].text);
  api->free_context(&context);
  require(has_preedit, "Missing preedit for " + keys);
  require(!result.empty(), "Missing candidates for " + keys);
  std::cout << keys << " -> " << result.front() << " (" << result.size() << " candidates)\n";
  return result;
}

std::string commit() {
  require(api->process_key(session, ' ', 0), "Space was not handled");
  RIME_STRUCT(RimeCommit, committed);
  require(api->get_commit(session, &committed), "No committed text");
  const std::string text = committed.text ? committed.text : "";
  api->free_commit(&committed);
  require(!text.empty(), "Empty committed text");
  return text;
}

void expect_candidate(const std::string& input, const std::string& expected) {
  compose(input);
  RimeCandidateListIterator iter{};
  require(api->candidate_list_begin(session, &iter), "Cannot enumerate candidates");
  bool found = false;
  while (api->candidate_list_next(&iter)) {
    if (expected == iter.candidate.text) {
      found = true;
      break;
    }
    if (iter.index > 100) break;
  }
  api->candidate_list_end(&iter);
  require(found, "Expected candidate missing: " + expected + " for " + input);
}
}  // namespace

int main(int argc, char** argv) {
  if (argc != 3) {
    std::cerr << "usage: shuangsheng_rime_smoke USER_DATA SHARED_DATA\n";
    return 2;
  }
  api = rime_get_api();
  RIME_STRUCT(RimeTraits, traits);
  traits.shared_data_dir = argv[2];
  traits.user_data_dir = argv[1];
  traits.app_name = "rime.shuangsheng_smoke";
  traits.min_log_level = 2;
  traits.log_dir = "";
  api->setup(&traits);
  api->set_notification_handler(notification, nullptr);
  api->initialize(&traits);
  try {
    if (api->start_maintenance(True)) api->join_maintenance_thread();
    require(!deploy_failed, "Rime deployment failed");
    RimeSchemaList schemas{};
    require(api->get_schema_list(&schemas), "Cannot load deployed schema list");
    bool present = false, retained = false;
    for (size_t i = 0; i < schemas.size; ++i) {
      present |= std::strcmp(schemas.list[i].schema_id, "shuangsheng") == 0;
      retained |= std::strcmp(schemas.list[i].schema_id, "existing") == 0;
    }
    api->free_schema_list(&schemas);
    require(present && retained, "Installer lost an existing schema or failed to add Shuangsheng");
    session = api->create_session();
    require(session != 0, "Cannot create Rime session");
    require(api->select_schema(session, "shuangsheng"), "Cannot select Shuangsheng");
    api->set_option(session, "ascii_mode", False);
    api->set_option(session, "simplification", True);
    expect_candidate("nihao", "你好");
    const auto first = compose("nihao");
    require(first.size() > 1, "Candidate selection has only one item");
    require(commit() == first.front(), "Space did not commit the first candidate");
    compose("shi");
    require(api->process_key(session, '=', 0), "Next-page key was not handled");
    RIME_STRUCT(RimeContext, second_page);
    require(api->get_context(session, &second_page), "Missing page context");
    const bool paged = second_page.menu.page_no == 1 && second_page.menu.num_candidates > 1;
    const std::string second = paged ? second_page.menu.candidates[1].text : "";
    api->free_context(&second_page);
    require(paged, "Candidates did not advance to page two");
    require(api->process_key(session, '2', 0), "Numeric selection was not handled");
    RIME_STRUCT(RimeCommit, selected);
    require(api->get_commit(session, &selected), "No commit after numeric selection");
    const std::string selected_text = selected.text ? selected.text : "";
    api->free_commit(&selected);
    require(selected_text == second, "Numeric selection committed the wrong candidate");
    expect_candidate("zhongguo", "中国");
    api->set_option(session, "simplification", False);
    expect_candidate("zhongguo", "中國");
    api->set_option(session, "simplification", True);
    expect_candidate("ni'hao", "你好");
    const auto sentence = compose("woxihuanzhongguowenhua");
    const auto text = commit();
    require(text == sentence.front() && text.size() >= 18,
            "Sentence composition failed: " + text);
    compose("nihaoo");
    require(api->process_key(session, 0xff08, 0), "Backspace not handled");
    require(std::string(api->get_input(session)) == "nihao", "Backspace did not edit input");
    require(api->process_key(session, 0xff1b, 0), "Escape not handled");
    require(!api->get_input(session) || !*api->get_input(session), "Escape did not clear input");
    require(api->process_key(session, ',', 0), "Chinese punctuation not handled");
    RIME_STRUCT(RimeCommit, punctuation);
    require(api->get_commit(session, &punctuation), "Missing punctuation commit");
    const std::string mark = punctuation.text ? punctuation.text : "";
    api->free_commit(&punctuation);
    require(mark == "，", "Wrong Chinese punctuation: " + mark);
    api->destroy_session(session);
    session = 0;
    api->finalize();
    std::cout << "PASS: deployment, schema preservation, candidates, simplified/traditional, "
                 "paging, numeric selection, sentence commit, editing, and punctuation\n";
    return 0;
  } catch (const std::exception& error) {
    if (session) api->destroy_session(session);
    api->finalize();
    std::cerr << "FAIL: " << error.what() << '\n';
    return 1;
  }
}
