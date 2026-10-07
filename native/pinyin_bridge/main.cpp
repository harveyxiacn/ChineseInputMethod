// SPDX-License-Identifier: MIT
// Read-only decoder bridge. A process owns one immutable local dictionary/model.
#include <libime/core/languagemodel.h>
#include <libime/core/userlanguagemodel.h>
#include <libime/core/prediction.h>
#include <libime/pinyin/pinyincontext.h>
#include <libime/pinyin/pinyindictionary.h>
#include <libime/pinyin/pinyinime.h>
#include <libime/pinyin/shuangpinprofile.h>
#include <iostream>
#include <sstream>
#include <stdexcept>
#include <string>

std::string hex(const std::string &value) {
    const char *digits = "0123456789abcdef";
    std::string result;
    for (unsigned char ch : value) { result += digits[ch >> 4]; result += digits[ch & 15]; }
    return result;
}
std::string unhex(const std::string &value) {
    if (value.size() % 2 || value.size() > 8192) throw std::runtime_error("Invalid protocol length");
    auto digit = [](char ch) { if (ch >= '0' && ch <= '9') return ch - '0';
        if (ch >= 'a' && ch <= 'f') return ch - 'a' + 10;
        throw std::runtime_error("Invalid protocol byte"); };
    std::string result;
    for (size_t i = 0; i < value.size(); i += 2) result += static_cast<char>((digit(value[i]) << 4) | digit(value[i+1]));
    return result;
}

void emit(libime::PinyinIME &ime, const std::string &query, const std::string &scheme,
          const std::string &previous, int limit, bool protocol) {
    if (query.size() > 512 || previous.size() > 1024 || limit < 1 || limit > 50 ||
        (scheme != "pinyin" && scheme != "shuangpin" && scheme != "predict")) throw std::runtime_error("Invalid request");
    if (scheme == "predict") {
        if (previous.empty()) return;
        libime::Prediction prediction;
        prediction.setUserLanguageModel(ime.model());
        for (const auto &text : prediction.predict(std::vector<std::string>{previous}, limit)) {
            if (text.empty() || text == previous) continue;
            std::cout << (protocol ? hex(text) : text) << '\t' << '\n';
        }
        return;
    }
    libime::PinyinContext context(&ime);
    context.setUseShuangpin(scheme == "shuangpin");
    if (!previous.empty()) context.setContextWords({previous});
    context.type(query);
    const auto &candidates = context.candidates();
    for (size_t i = 0; i < candidates.size() && static_cast<int>(i) < limit; ++i) {
        // Incremental phrase suggestions belong to the native frontend; this
        // bridge returns candidates that consume the whole input only.
        libime::PinyinContext check(&ime);
        check.setUseShuangpin(context.useShuangpin());
        if (!previous.empty()) check.setContextWords({previous});
        check.type(query); check.select(i);
        if (!check.selected()) continue;
        auto text = candidates[i].toString(); auto code = context.candidateFullPinyin(i);
        std::cout << (protocol ? hex(text) : text) << '\t' << (protocol ? hex(code) : code) << '\n';
    }
}

int main(int argc, char **argv) {
    bool server = argc == 2 && std::string(argv[1]) == "--serve";
    if (!server && argc != 5) return 2;
    try {
        auto file = libime::DefaultLanguageModelResolver::instance().languageModelFileForLanguage("zh_CN");
        if (!file) throw std::runtime_error("No local Chinese language model");
        libime::PinyinIME ime(std::make_unique<libime::PinyinDictionary>(),
                             std::make_unique<libime::UserLanguageModel>(std::move(file)));
        ime.dict()->load(libime::PinyinDictionary::SystemDict, PINYIN_DICT, libime::PinyinDictFormat::Binary);
        ime.setNBest(20); ime.setFuzzyFlags(libime::PinyinFuzzyFlags());
        ime.setShuangpinProfile(std::make_shared<libime::ShuangpinProfile>(libime::ShuangpinBuiltinProfile::Xiaohe));
        if (!server) { emit(ime, argv[1], argv[2], argv[3], std::stoi(argv[4]), false); return 0; }
        std::string line;
        while (std::getline(std::cin, line)) {
            try {
                if (line.size() > 8192) throw std::runtime_error("Oversized request");
                std::istringstream input(line); std::string query, scheme, previous, count;
                if (!std::getline(input, query, '\t') || !std::getline(input, scheme, '\t') ||
                    !std::getline(input, previous, '\t') || !std::getline(input, count) || count.empty())
                    throw std::runtime_error("Malformed request");
                size_t consumed = 0; int limit = std::stoi(count, &consumed);
                if (consumed != count.size()) throw std::runtime_error("Invalid count");
                emit(ime, unhex(query), scheme, unhex(previous), limit, true);
            } catch (const std::exception &) { std::cout << "!\n"; }
            std::cout << ".\n" << std::flush;
        }
        return 0;
    } catch (const std::exception &e) { std::cerr << e.what() << '\n'; return 1; }
}
