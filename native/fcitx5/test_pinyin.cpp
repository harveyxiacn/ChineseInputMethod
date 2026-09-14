// SPDX-License-Identifier: MIT
#include "pinyin.h"
#include <stdexcept>
#include <unistd.h>

void require(bool condition, const char *message) {
    if (!condition) throw std::runtime_error(message);
}
int main() {
    auto storage=std::filesystem::temp_directory_path()/ ("shuangsheng-pinyin-test-"+std::to_string(getpid()));
    try {
        {
            shuangsheng::Pinyin pinyin(storage);
            libime::PinyinContext context(pinyin.ime());
            // Sentences are composed from the full dictionary and language model;
            // none of these tests inject dictionary rows or candidate overrides.
            for (const char *input : {"wojintianxiangquchaoshimaidongxi", "mingtianwomenyiqiquchifan", "rengongzhineng", "zhonghuarenmingongheguo"}) {
                context.type(input);
                require(!context.candidates().empty(), "Missing sentence candidates");
                auto sentence=context.candidates()[0].toString();
                std::cout << input << " -> " << sentence << '\n';
                require(sentence.size()>9, "Sentence unexpectedly truncated");
                context.select(0);
                require(context.selected(), "Top sentence did not consume all pinyin");
                require(context.selectedSentence()==sentence, "Selected sentence changed");
                context.learn(); context.clear();
            }
            context.type("nihaoshijie");
            size_t index=0;
            while(index<context.candidates().size() && context.candidates()[index].toString()!="你好") ++index;
            require(index<context.candidates().size(), "Missing phrase prefix candidate");
            context.select(index);
            require(!context.selected() && context.selectedSentence()=="你好", "Phrase selection lost remaining pinyin");
            index=0;
            while(index<context.candidates().size() && context.candidates()[index].toString()!="世界") ++index;
            require(index<context.candidates().size(), "Missing remaining phrase candidate");
            context.select(index);
            require(context.selected() && context.selectedSentence()=="你好世界", "Remaining phrase did not compose");
            context.learn(); context.clear();
            require(pinyin.display("人工智能软件",true)=="人工智能軟件", "Traditional conversion failed");
            // Persist a deliberately new learned phrase, then reopen the backend.
            pinyin.ime()->dict()->addWord(libime::PinyinDictionary::UserDict,"shuang'sheng'ce'shi","双声测诗");
            pinyin.ime()->model()->history().add(std::vector<std::string>{"双声测诗"});
            pinyin.save();
        }
        {
            shuangsheng::Pinyin restored(storage);
            require(restored.ime()->dict()->lookupWord(libime::PinyinDictionary::UserDict,"shuang'sheng'ce'shi","双声测诗").has_value(), "User dictionary did not survive restart");
            require(!restored.ime()->model()->history().isUnknown("双声测诗"), "Learning history did not survive restart");
        }
        std::filesystem::remove_all(storage);
    } catch (...) {
        std::filesystem::remove_all(storage);
        throw;
    }
}
