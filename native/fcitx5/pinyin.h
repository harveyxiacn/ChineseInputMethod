// SPDX-License-Identifier: MIT
#pragma once
#include "paths.h"
#include <libime/core/historybigram.h>
#include <libime/core/languagemodel.h>
#include <libime/core/userlanguagemodel.h>
#include <libime/pinyin/pinyincontext.h>
#include <libime/pinyin/pinyindictionary.h>
#include <libime/pinyin/pinyinime.h>
#include <opencc/SimpleConverter.hpp>
#include <cstdlib>
#include <filesystem>
#include <fstream>
#include <iostream>
#include <stdexcept>

namespace shuangsheng {
// Share the system dictionary and statistical model across input contexts.
// Keep private learning separate from the distribution's immutable data.
class Pinyin {
public:
    explicit Pinyin(std::filesystem::path storage = defaultStorage())
        : ime_(std::make_unique<libime::PinyinDictionary>(),
               makeModel()),
          storage_(std::move(storage)), traditional_("s2t.json") {
        ime_.dict()->load(libime::PinyinDictionary::SystemDict,
                          SHUANGSHENG_PINYIN_DICT, libime::PinyinDictFormat::Binary);
        ime_.setNBest(3);
        ime_.setFuzzyFlags({libime::PinyinFuzzyFlag::Inner, libime::PinyinFuzzyFlag::CommonTypo});
        if (!storage_.empty()) {
            try {
                if (std::filesystem::exists(storage_ / "user.dict"))
                    ime_.dict()->load(libime::PinyinDictionary::UserDict,
                        (storage_ / "user.dict").c_str(), libime::PinyinDictFormat::Binary);
            } catch (const std::exception &e) { std::cerr << "ShuangSheng user dictionary: " << e.what() << '\n'; }
            try {
                std::ifstream input(storage_ / "history", std::ios::binary);
                if (input) ime_.model()->history().load(input);
            } catch (const std::exception &e) { std::cerr << "ShuangSheng history: " << e.what() << '\n'; }
        }
    }
    libime::PinyinIME *ime() { return &ime_; }
    std::string display(const std::string &text, bool traditional) const {
        return traditional ? traditional_.Convert(text) : text;
    }
    void save() {
        if (storage_.empty()) return;
        try {
            std::filesystem::create_directories(storage_);
            std::filesystem::permissions(storage_, std::filesystem::perms::owner_all);
            // Atomic replacement keeps the previous file intact if saving fails.
            ime_.dict()->save(libime::PinyinDictionary::UserDict,
                (storage_ / "user.dict.tmp").c_str(), libime::PinyinDictFormat::Binary);
            std::filesystem::rename(storage_ / "user.dict.tmp", storage_ / "user.dict");
            std::ofstream output(storage_ / "history.tmp", std::ios::binary);
            output.exceptions(std::ios::badbit | std::ios::failbit);
            ime_.model()->history().save(output);
            output.close();
            std::filesystem::rename(storage_ / "history.tmp", storage_ / "history");
        } catch (const std::exception &e) { std::cerr << "ShuangSheng cannot save learning: " << e.what() << '\n'; }
    }
private:
    static std::unique_ptr<libime::UserLanguageModel> makeModel() {
        auto model=libime::DefaultLanguageModelResolver::instance().languageModelFileForLanguage("zh_CN");
        if (!model) throw std::runtime_error("Install the libime Chinese language model (zh_CN.lm)");
        return std::make_unique<libime::UserLanguageModel>(std::move(model));
    }
    static std::filesystem::path defaultStorage() {
        const char *data = std::getenv("XDG_DATA_HOME");
        if (data && *data && std::filesystem::path(data).is_absolute())
            return std::filesystem::path(data) / "shuangsheng";
        const char *home = std::getenv("HOME");
        return home && *home ? std::filesystem::path(home) / ".local/share/shuangsheng" : std::filesystem::path{};
    }
    libime::PinyinIME ime_;
    std::filesystem::path storage_;
    opencc::SimpleConverter traditional_;
};
}
