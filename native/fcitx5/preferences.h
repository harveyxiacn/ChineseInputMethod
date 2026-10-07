// SPDX-License-Identifier: MIT
#pragma once
#include "paths.h"
#include <json-c/json.h>
#include <cstdlib>
#include <cstring>
#include <iostream>
#include <algorithm>
#include <chrono>
#include <cmath>
#include <ctime>
#include <filesystem>
#include <fstream>
#include <map>
#include <set>
#include <string>
#include <vector>
#include <fcntl.h>
#include <sys/file.h>
#include <unistd.h>

namespace shuangsheng {
inline std::string jsonString(json_object *object, const char *key, const std::string &fallback={}) {
    json_object *value=nullptr;
    return object && json_object_object_get_ex(object,key,&value) && json_object_is_type(value,json_type_string)
        ? json_object_get_string(value) : fallback;
}
inline int jsonInt(json_object *object,const char *key,int fallback) {
    json_object *value=nullptr;
    return object && json_object_object_get_ex(object,key,&value) && json_object_is_type(value,json_type_int)
        ? json_object_get_int(value) : fallback;
}
inline bool jsonBool(json_object *object,const char *key,bool fallback=false) {
    json_object *value=nullptr;
    return object && json_object_object_get_ex(object,key,&value) && json_object_is_type(value,json_type_boolean)
        ? json_object_get_boolean(value) : fallback;
}
inline std::string compactCode(std::string value,bool tones=true) {
    value.erase(std::remove_if(value.begin(),value.end(),[tones](unsigned char c){return c==' '||c=='\''||(!tones && c>='1' && c<='6');}),value.end());
    return value;
}
inline std::string contextTail(const std::string &value) {
    size_t count=0,position=value.size();
    while(position && count<120){--position;while(position && (static_cast<unsigned char>(value[position])&0xc0)==0x80)--position;++count;}
    return value.substr(position);
}
inline bool englishCode(const std::string &value) {
    static const std::set<std::string> words={"api","python","javascript","typescript","timeout","hello","world","docker","git","github","linux","windows","macos","vscode","chatgpt","http","https","www","test","email","version","update","commit","review","server","client","token","debug","json","html","css","npm","pip","node","install","function","return","class","const","async","await","sql","select","from","where"};
    return words.count(value) || value.find_first_of("ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789@_/:\\.-")!=std::string::npos;
}
struct Preferences {
    std::string language="zh",scheme="pinyin",inputMode="chinese",hotkey="Control+Alt+space";
    bool traditional=false,fuzzy=false,pushToTalk=false;
    int count=9;
    std::vector<std::string> fuzzyPairs={"zh-z","ch-c","sh-s","n-l","ang-an","eng-en","ing-in"};
};
struct PersonalWord {std::string text,code,shortcut; bool pinned=false;};
struct LearnEvent {std::string query,text,context;};
class PersonalData {
public:
    PersonalData() {
        const char *overridePath=std::getenv("IME_SETTINGS_PATH");
        const char *xdg=std::getenv("XDG_CONFIG_HOME"), *home=std::getenv("HOME");
        auto directory=xdg&&*xdg ? std::filesystem::path(xdg) : std::filesystem::path(home?home:"/tmp")/".config";
        directory/="shuangsheng";
        settings_=overridePath&&*overridePath ? overridePath : directory/"settings.json";
        const char *lex=std::getenv("IME_LEXICON_PATH");
        lexicon_=lex&&*lex ? lex : directory/"lexicon.json";
        reload();
    }
    ~PersonalData(){if(config_) json_object_put(config_); if(lex_) json_object_put(lex_);}
    void reload() {
        auto now=std::chrono::steady_clock::now();
        if(now-lastRead_<std::chrono::milliseconds(500)) return;
        lastRead_=now;
        auto replace=[](json_object *&old,const std::filesystem::path &path){
            auto *value=json_object_from_file(path.c_str());
            if(value && json_object_is_type(value,json_type_object)){if(old)json_object_put(old);old=value;}
            else if(value)json_object_put(value);
        };
        replace(config_,settings_); replace(lex_,lexicon_);
    }
    Preferences preferences(const std::string &program) {
        reload(); Preferences prefs; apply(prefs,config_);
        json_object *profiles=nullptr,*profile=nullptr;
        if(config_ && json_object_object_get_ex(config_,"app_profiles",&profiles)
            && json_object_object_get_ex(profiles,program.c_str(),&profile)) apply(prefs,profile);
        return prefs;
    }
    void set(const char *key,const std::string &value) {
        mutate(settings_,[&](json_object *object){json_object_object_add(object,key,json_object_new_string(value.c_str()));});
        lastRead_={}; reload();
    }
    std::vector<PersonalWord> words(const std::string &query,const std::string &context) {
        reload(); std::vector<PersonalWord> result;
        json_object *entries=nullptr;
        if(lex_ && json_object_object_get_ex(lex_,"entries",&entries) && json_object_is_type(entries,json_type_array)) {
            for(size_t i=0;i<json_object_array_length(entries);++i){
                auto *row=json_object_array_get_idx(entries,i);
                PersonalWord word{jsonString(row,"text"),jsonString(row,"pinyin"),jsonString(row,"shortcut"),jsonBool(row,"pinned")};
                if(word.text.empty())continue;
                if((!query.empty() && ((!word.code.empty() && compactCode(word.code)==compactCode(query)) || word.shortcut==query)) || (query.empty() && context.empty() && word.pinned)) result.push_back(word);
            }
        }
        json_object *history=nullptr;
        if(lex_ && json_object_object_get_ex(lex_,"learn",&history) && json_object_is_type(history,json_type_array)) {
            for(size_t i=json_object_array_length(history);i>0;--i){
                auto *row=json_object_array_get_idx(history,i-1);
                if((!query.empty() && compactCode(jsonString(row,"query"))==compactCode(query)) || (query.empty() && !context.empty() && jsonString(row,"context")==context)) {
                    auto text=jsonString(row,"text");
                    if(!text.empty() && std::none_of(result.begin(),result.end(),[&](const auto &word){return word.text==text;})) result.push_back({text,jsonString(row,"query"),"",false});
                }
                if(result.size()>=20)break;
            }
        }
        std::stable_sort(result.begin(),result.end(),[](const auto &a,const auto &b){return a.pinned>b.pinned;});
        if(query=="/date" || query=="/time"){
            auto now=std::time(nullptr); std::tm tm{}; localtime_r(&now,&tm);char buffer[64];
            std::strftime(buffer,sizeof(buffer),query=="/date"?"%Y-%m-%d":"%H:%M",&tm); result.insert(result.begin(),{buffer,query,"",true});
        }
        return result;
    }
    void learn(std::string query,std::string text,std::string context) {
        if(query.empty()||text.empty()||query.size()>256||text.size()>1536)return;
        context=contextTail(context);
        pending_.push_back({std::move(query),std::move(text),std::move(context)});
    }
    void flush() {
        if(pending_.empty())return;
        bool saved=mutate(lexicon_,[&](json_object *object){
            json_object *history=nullptr;
            if(!json_object_object_get_ex(object,"learn",&history)||!json_object_is_type(history,json_type_array)){
                history=json_object_new_array();json_object_object_add(object,"learn",history);
            }
            for(const auto &event:pending_){
                json_object *row=nullptr;
                for(size_t i=0;i<json_object_array_length(history);++i){auto *item=json_object_array_get_idx(history,i);
                    if(jsonString(item,"query")==event.query && jsonString(item,"text")==event.text && jsonString(item,"context")==event.context){row=item;break;}}
                if(!row){row=json_object_new_object();json_object_object_add(row,"query",json_object_new_string(event.query.c_str()));json_object_object_add(row,"text",json_object_new_string(event.text.c_str()));json_object_object_add(row,"context",json_object_new_string(event.context.c_str()));json_object_array_add(history,row);}
                json_object_object_add(row,"count",json_object_new_int(std::min(1000000000,jsonInt(row,"count",0)+1)));
            }
            auto length=json_object_array_length(history);if(length>2000)json_object_array_del_idx(history,0,length-2000);
        });
        if(saved){pending_.clear();lastRead_={};reload();}
    }
    std::vector<std::string> jyutping(const std::string &query,size_t limit=9) {
        if(!jyutpingLoaded_){
            std::ifstream input(std::string(SHUANGSHENG_PROJECT_ROOT)+"/ime/data/jyutping.tsv");std::string line;
            while(std::getline(input,line)){if(line.empty()||line[0]=='#')continue;auto first=line.find('\t'),last=line.rfind('\t');if(first==std::string::npos||last==first)continue;
                auto text=line.substr(0,first),code=compactCode(line.substr(first+1,last-first-1));
                double weight=1;try{weight=std::stod(line.substr(last+1));}catch(...){}
                jyutping_[code].push_back({text,weight}); auto bare=compactCode(code,false); if(bare!=code)jyutping_[bare].push_back({text,weight});
            }jyutpingLoaded_=true;
        }
        std::vector<std::string> result;auto it=jyutping_.find(compactCode(query));
        if(it!=jyutping_.end()) for(const auto &[word,weight]:it->second){if(std::find(result.begin(),result.end(),word)==result.end())result.push_back(word);if(result.size()>=limit)break;}
        auto key=compactCode(query);
        if(key.size()>128)return {};
        std::vector<std::vector<std::pair<double,std::string>>> beams(key.size()+1);
        beams[0].push_back({0,""});
        for(size_t start=0;start<key.size();++start){
            if(beams[start].empty())continue;
            for(size_t end=start+1;end<=std::min(key.size(),start+40);++end){
                auto match=jyutping_.find(key.substr(start,end-start));if(match==jyutping_.end())continue;
                size_t choices=0;
                for(const auto &[word,weight]:match->second){
                    for(const auto &[score,text]:beams[start])beams[end].push_back({score+std::log1p(weight)-12,text+word});
                    if(++choices>=5)break;
                }
                auto &bucket=beams[end];std::sort(bucket.begin(),bucket.end(),[](const auto &a,const auto &b){return a.first>b.first;});if(bucket.size()>limit)bucket.resize(limit);
            }
        }
        for(const auto &[score,text]:beams.back()){if(std::find(result.begin(),result.end(),text)==result.end())result.push_back(text);if(result.size()>=limit)break;}
        if(result.size()>limit)result.resize(limit);
        return result;
    }
private:
    static void apply(Preferences &prefs,json_object *object){
        prefs.language=jsonString(object,"language",prefs.language);prefs.scheme=jsonString(object,"input_scheme",prefs.scheme);
        prefs.traditional=jsonString(object,"script",prefs.traditional?"traditional":"simplified")=="traditional";
        prefs.inputMode=jsonString(object,"input_mode",prefs.inputMode);prefs.count=std::clamp(jsonInt(object,"candidate_count",prefs.count),1,9);
        prefs.hotkey=jsonString(object,"hotkey",prefs.hotkey);
        auto replaceKey=[&](const std::string &from,const std::string &to){auto pos=prefs.hotkey.find(from);if(pos!=std::string::npos)prefs.hotkey.replace(pos,from.size(),to);};
        replaceKey("Ctrl","Control");replaceKey("Space","space");
        json_object *pairs=nullptr;
        if(object&&json_object_object_get_ex(object,"fuzzy_pairs",&pairs)&&json_object_is_type(pairs,json_type_array)){
            prefs.fuzzyPairs.clear();for(size_t i=0;i<json_object_array_length(pairs);++i){auto *value=json_object_array_get_idx(pairs,i);if(json_object_is_type(value,json_type_string))prefs.fuzzyPairs.emplace_back(json_object_get_string(value));}
        }
        prefs.fuzzy=jsonBool(object,"fuzzy",prefs.fuzzy);prefs.pushToTalk=jsonBool(object,"push_to_talk",prefs.pushToTalk);
    }
    template<class Function> bool mutate(const std::filesystem::path &path,Function function){
        try{
            std::filesystem::create_directories(path.parent_path());auto lock=path.string()+".lock";
            int fd=open(lock.c_str(),O_CREAT|O_RDWR|O_CLOEXEC|O_NOFOLLOW,0600);if(fd<0)return false;
            if(flock(fd,LOCK_EX)<0){close(fd);return false;}
            auto *object=json_object_from_file(path.c_str());if(!object||!json_object_is_type(object,json_type_object)){if(object)json_object_put(object);object=json_object_new_object();}
            function(object);auto temp=path.string()+".native-"+std::to_string(getpid())+".tmp";
            int out=open(temp.c_str(),O_WRONLY|O_CREAT|O_EXCL|O_CLOEXEC,0600);bool ok=out>=0;
            if(ok){const char *bytes=json_object_to_json_string_ext(object,JSON_C_TO_STRING_PRETTY);size_t remaining=strlen(bytes);while(remaining){auto n=write(out,bytes,remaining);if(n<=0){ok=false;break;}bytes+=n;remaining-=n;}if(ok)ok=fsync(out)==0;close(out);}
            if(ok)ok=rename(temp.c_str(),path.c_str())==0;
            unlink(temp.c_str());json_object_put(object);flock(fd,LOCK_UN);close(fd);return ok;
        }catch(const std::exception &e){std::cerr<<"Shuangsheng preferences: "<<e.what()<<'\n';return false;}
    }
    std::filesystem::path settings_,lexicon_;
    json_object *config_=nullptr,*lex_=nullptr;
    std::chrono::steady_clock::time_point lastRead_{};
    std::vector<LearnEvent> pending_;
    bool jyutpingLoaded_=false;
    std::map<std::string,std::vector<std::pair<std::string,double>>> jyutping_;
};
}
