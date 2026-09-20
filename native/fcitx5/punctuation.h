// SPDX-License-Identifier: MIT
#pragma once
#include <string>
namespace shuangsheng {
struct Punctuation {
    bool doubleOpen=true, singleOpen=true, afterDigit=false;
    std::string convert(unsigned int key) {
        bool decimal=afterDigit && key=='.';
        afterDigit=key>='0' && key<='9';
        if(decimal) return {};
        switch(key) {
        case ',': return "，";
        case '.': return "。";
        case '?': return "？";
        case '!': return "！";
        case ':': return "：";
        case ';': return "；";
        case '(': return "（";
        case ')': return "）";
        case '[': return "【";
        case ']': return "】";
        case '<': return "《";
        case '>': return "》";
        case '\\': return "、";
        case '^': return "……";
        case '_': return "——";
        case '"': doubleOpen=!doubleOpen; return doubleOpen ? "”" : "“";
        case '\'': singleOpen=!singleOpen; return singleOpen ? "’" : "‘";
        default: return {};
        }
    }
};
}
