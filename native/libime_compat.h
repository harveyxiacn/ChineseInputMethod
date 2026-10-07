// SPDX-License-Identifier: MIT
#pragma once
#include <string>
#include <vector>

namespace shuangsheng {
// libime 1.1.5 composes sentences but has no previous-commit context API.
// Keep its normal sentence ranking, personal ranking and explicit predictions;
// use cross-commit decoder context only when it can also be cleared on reset
// or entry into a sensitive field.
template <typename Context>
inline constexpr bool hasPinyinContextWords =
    requires(Context &context, const std::vector<std::string> &words) {
        context.setContextWords(words);
        context.clearContextWords();
    };

template <typename Context>
bool setPinyinContextWords(Context &context,
                          const std::vector<std::string> &words) {
    if constexpr (hasPinyinContextWords<Context>) {
        context.setContextWords(words);
        return true;
    }
    return false;
}

template <typename Context>
bool clearPinyinContextWords(Context &context) {
    if constexpr (hasPinyinContextWords<Context>) {
        context.clearContextWords();
        return true;
    }
    return false;
}
} // namespace shuangsheng
