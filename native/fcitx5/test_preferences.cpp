// SPDX-License-Identifier: MIT
#include "preferences.h"
#include <stdexcept>
void require(bool result,const char *message){if(!result)throw std::runtime_error(message);}
int main(){
    auto directory=std::filesystem::temp_directory_path()/("shuangsheng-preferences-"+std::to_string(getpid()));
    std::filesystem::create_directories(directory);
    auto settings=directory/"settings.json",lexicon=directory/"lexicon.json";
    setenv("IME_SETTINGS_PATH",settings.c_str(),1);setenv("IME_LEXICON_PATH",lexicon.c_str(),1);
    try{
        std::ofstream(settings)<<R"({"language":"yue","script":"traditional","input_scheme":"shuangpin","candidate_count":5,"hotkey":"Ctrl+Alt+Space","app_profiles":{"terminal":{"input_mode":"english"}}})";
        std::ofstream(lexicon)<<R"({"entries":[{"id":"test","text":"雙聲","pinyin":"shuang sheng","shortcut":"/ss","pinned":true}],"learn":[]})";
        shuangsheng::PersonalData data;
        auto prefs=data.preferences("editor");require(prefs.language=="yue"&&prefs.traditional&&prefs.scheme=="shuangpin"&&prefs.count==5,"Preferences not loaded");
        require(prefs.hotkey=="Control+Alt+space","Hotkey normalization failed");
        require(data.preferences("terminal").inputMode=="english","Per-app mode missing");
        require(data.words("shuangsheng","").at(0).text=="雙聲","Personal word missing");
        require(data.words("/ss","").at(0).text=="雙聲","Shortcut missing");
        data.set("language","zh");require(data.preferences("editor").language=="zh","Preference not persisted");
        require(data.preferences("terminal").inputMode=="english","Saving preference erased profiles");
        data.learn("nihao","你好","前文");data.learn("nihao","你好","前文");data.flush();
        auto words=data.words("","前文");require(words.size()==1&&words[0].text=="你好","Prediction missing");
        require(data.words("","不是前文").empty(),"Context prediction crossed negation");
        auto *json=json_object_from_file(lexicon.c_str());json_object *history=nullptr;json_object_object_get_ex(json,"learn",&history);require(shuangsheng::jsonInt(json_object_array_get_idx(history,0),"count",0)==2,"Duplicate learning lost");json_object_put(json);
        auto jyutping=data.jyutping("nei5hou2");require(std::find(jyutping.begin(),jyutping.end(),"你好")!=jyutping.end(),"Jyutping tones missing");
        require(shuangsheng::englishCode("Python3.12")&&shuangsheng::englishCode("test@example.com")&&!shuangsheng::englishCode("nihao"),"Mixed input recognition failed");
        require(shuangsheng::contextTail(std::string(130,'x')+"你好").size()==124,"Context UTF8 boundary wrong");
        std::filesystem::remove_all(directory);
    }catch(...){std::filesystem::remove_all(directory);throw;}
}
