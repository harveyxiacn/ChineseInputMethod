// SPDX-License-Identifier: MIT
#include "paths.h"
#include <fcitx/addonfactory.h>
#include <fcitx/addonmanager.h>
#include <fcitx/candidatelist.h>
#include <fcitx/inputcontext.h>
#include <fcitx/inputcontextmanager.h>
#include <fcitx/inputcontextproperty.h>
#include <fcitx/inputmethodengine.h>
#include <fcitx/inputpanel.h>
#include <fcitx/instance.h>
#include <fcitx-utils/capabilityflags.h>
#include <fcitx-utils/event.h>
#include <algorithm>
#include <cerrno>
#include <csignal>
#include <fcntl.h>
#include <fstream>
#include <sstream>
#include <string>
#include <unordered_set>
#include <vector>
#include <sys/wait.h>
#include <unistd.h>

namespace {
using namespace fcitx;
struct Entry { std::string simple, traditional, key, initials; std::vector<size_t> boundaries; double weight; };
struct State : InputContextProperty { std::string buffer; };
class Engine;
class Word : public CandidateWord {
public:
    Word(std::string text, Engine *engine) : CandidateWord(Text(text)), value_(std::move(text)), engine_(engine) {}
    void select(InputContext *ic) const override;
private:
    std::string value_;
    Engine *engine_;
};
class Engine : public InputMethodEngine {
public:
    explicit Engine(Instance *instance) : instance_(instance) {
        instance_->inputContextManager().registerProperty("shuangsheng", &factory_);
        for (const char *file : {"starter.tsv", "rime.tsv"}) {
            std::ifstream input(std::string(SHUANGSHENG_PROJECT_ROOT) + "/ime/data/" + file);
            std::string line;
            while (std::getline(input, line)) {
                if (line.empty() || line[0] == '#') continue;
                std::istringstream row(line); Entry e; std::string pinyin, weight;
                if (!std::getline(row,e.simple,'\t') || !std::getline(row,e.traditional,'\t') ||
                    !std::getline(row,pinyin,'\t') || !std::getline(row,weight)) continue;
                try { e.weight=std::stod(weight); } catch (...) { continue; }
                std::istringstream syllables(pinyin); std::string syllable;
                while (syllables >> syllable) { if (!e.key.empty()) e.boundaries.push_back(e.key.size()); e.key += syllable; e.initials += syllable[0]; }
                entries_.push_back(std::move(e));
            }
        }
        std::stable_sort(entries_.begin(), entries_.end(), [](const Entry &a,const Entry &b){return a.weight>b.weight;});
        for (auto type : {EventType::InputContextFocusOut, EventType::InputContextDestroyed,
                          EventType::InputContextCapabilityChanged}) {
            handlers_.push_back(instance_->watchEvent(type, EventWatcherPhase::PreInputMethod,
                [this](Event &event) {
                    auto *ic=static_cast<InputContextEvent &>(event).inputContext();
                    if (ic == voiceContext_) cancelVoice();
                }));
        }
        timer_ = instance_->eventLoop().addTimeEvent(CLOCK_MONOTONIC, now(CLOCK_MONOTONIC)+100000, 0,
            [this](EventSourceTime *event, uint64_t) {
                pollVoice(); event->setNextInterval(100000); event->setOneShot(); return true;
            });
    }
    ~Engine() override {
        cancelVoice();
        if (pid_ > 0) { kill(-pid_, SIGKILL); while (waitpid(pid_, nullptr, 0)<0 && errno==EINTR) {} }
        if (voiceFd_ >= 0) close(voiceFd_);
    }
    void reset(const InputMethodEntry &, InputContextEvent &event) override {
        if (voiceContext_ == event.inputContext()) cancelVoice();
        clear(event.inputContext());
    }
    void commit(InputContext *ic, const std::string &text) {
        // Candidate selection may destroy the CandidateWord; copy before clearing UI.
        auto value=text; clear(ic); if (ic->hasFocus() && !sensitive(ic)) ic->commitString(value);
    }
    void keyEvent(const InputMethodEntry &, KeyEvent &event) override {
        if (event.isRelease()) return;
        auto *ic=event.inputContext();
        if (sensitive(ic)) { if (voiceContext_==ic) cancelVoice(); clear(ic); return; }
        auto key=event.key(); auto *state=ic->propertyFor(&factory_);
        if (key.check(Key("Control+Alt+space"))) {
            event.filterAndAccept();
            if (pid_ > 0) {
                if (voiceContext_==ic && !stopping_) { stopping_=true; status(ic,"Transcribing… Esc cancels"); }
            } else startVoice(ic);
            return;
        }
        if (key.check(Key("Control+Alt+M")) || key.check(Key("Control+Alt+Y")) || key.check(Key("Control+Alt+T"))) {
            event.filterAndAccept();
            if (pid_ > 0) return;
            if (key.check(Key("Control+Alt+T"))) traditional_=!traditional_;
            else language_=key.check(Key("Control+Alt+Y")) ? "yue" : "zh";
            update(ic); status(ic,language_=="yue" ? "Cantonese 粤语" : "Mandarin 普通话"); return;
        }
        if (key.check(FcitxKey_Escape)) {
            if (voiceContext_==ic || !state->buffer.empty()) { cancelVoice(); clear(ic); event.filterAndAccept(); }
            return;
        }
        if (voiceContext_==ic) return;
        if (key.states() & (KeyStates(KeyState::Ctrl) | KeyState::Alt | KeyState::Super)) return;
        auto sym=key.sym();
        if ((sym>=FcitxKey_a && sym<=FcitxKey_z) || (sym==FcitxKey_apostrophe && !state->buffer.empty())) {
            if (state->buffer.size()<128) state->buffer += static_cast<char>(sym);
            update(ic); event.filterAndAccept(); return;
        }
        if (state->buffer.empty()) return;
        auto list=ic->inputPanel().candidateList();
        if (sym==FcitxKey_BackSpace) { state->buffer.pop_back(); update(ic); event.filterAndAccept(); return; }
        if (sym==FcitxKey_Return || sym==FcitxKey_KP_Enter) { commit(ic,state->buffer); event.filterAndAccept(); return; }
        if (list && !list->empty()) {
            int index=-1;
            if (sym>=FcitxKey_1 && sym<=FcitxKey_9) index=static_cast<int>(sym-FcitxKey_1);
            if (sym==FcitxKey_space) index=std::max(0,list->cursorIndex());
            if (index>=0) { if(index<list->size()) list->candidate(index).select(ic); event.filterAndAccept(); return; }
            if (sym==FcitxKey_Page_Down || sym==FcitxKey_equal || sym==FcitxKey_Page_Up || sym==FcitxKey_minus) {
                auto *pages=list->toPageable();
                if (sym==FcitxKey_Page_Down || sym==FcitxKey_equal) { if (pages->hasNext()) pages->next(); }
                else if (pages->hasPrev()) pages->prev();
                ic->updateUserInterface(UserInterfaceComponent::InputPanel); event.filterAndAccept(); return;
            }
            if (sym==FcitxKey_Up || sym==FcitxKey_Down || sym==FcitxKey_Tab) {
                auto *cursor=list->toCursorMovable();
                if (sym==FcitxKey_Up) cursor->prevCandidate(); else cursor->nextCandidate();
                ic->updateUserInterface(UserInterfaceComponent::InputPanel); event.filterAndAccept(); return;
            }
        }
        if (sym==FcitxKey_space) { commit(ic,state->buffer); event.filterAndAccept(); return; }
        // Commit composition before ordinary punctuation, then let the app handle it.
        if (sym>=FcitxKey_exclam && sym<=FcitxKey_asciitilde) {
            if(list && !list->empty()) list->candidate(std::max(0,list->cursorIndex())).select(ic);
            else commit(ic,state->buffer);
        }
    }
private:
    static bool sensitive(InputContext *ic) {
        return bool(ic->capabilityFlags() & (CapabilityFlags(CapabilityFlag::Password) | CapabilityFlag::Sensitive));
    }
    void clear(InputContext *ic) {
        ic->propertyFor(&factory_)->buffer.clear(); ic->inputPanel().reset();
        ic->updatePreedit(); ic->updateUserInterface(UserInterfaceComponent::InputPanel);
    }
    void status(InputContext *ic, const std::string &text) {
        ic->inputPanel().setAuxUp(Text(text + (traditional_ ? " · 繁" : " · 简")));
        ic->updateUserInterface(UserInterfaceComponent::InputPanel);
    }
    void update(InputContext *ic) {
        const auto &buffer=ic->propertyFor(&factory_)->buffer;
        ic->inputPanel().reset(); Text preedit(buffer); preedit.setCursor(buffer.size());
        ic->inputPanel().setClientPreedit(preedit); ic->inputPanel().setPreedit(preedit);
        std::string query; std::vector<size_t> boundaries;
        for (char c:buffer) { if(c!='\'') query+=c; else boundaries.push_back(query.size()); }
        auto list=std::make_unique<CommonCandidateList>(); list->setPageSize(9);
        list->setSelectionKey({Key(FcitxKey_1),Key(FcitxKey_2),Key(FcitxKey_3),Key(FcitxKey_4),Key(FcitxKey_5),Key(FcitxKey_6),Key(FcitxKey_7),Key(FcitxKey_8),Key(FcitxKey_9)});
        std::unordered_set<std::string> seen;
        if (!query.empty()) for (int pass=0;pass<3;pass++) {
            for (const auto &entry:entries_) {
                bool match=pass==0 ? entry.key==query : pass==1 ? entry.initials==query : entry.key.compare(0,query.size(),query)==0;
                if (!boundaries.empty()) {
                    if (pass==1) match=false;
                    for (auto boundary:boundaries) if (std::find(entry.boundaries.begin(),entry.boundaries.end(),boundary)==entry.boundaries.end()) match=false;
                }
                const auto &text=traditional_ ? entry.traditional : entry.simple;
                if(match && seen.insert(text).second) list->append(std::make_unique<Word>(text,this));
                if(seen.size()>=90) break;
            }
            if(seen.size()>=90) break;
        }
        if (!list->empty()) list->setGlobalCursorIndex(0);
        ic->inputPanel().setCandidateList(std::move(list));
        if (!buffer.empty()) status(ic,language_=="yue" ? "粤语 · Ctrl+Alt+Space 录音" : "普通话 · Ctrl+Alt+Space 录音");
        ic->updatePreedit(); ic->updateUserInterface(UserInterfaceComponent::InputPanel);
    }
    void startVoice(InputContext *ic) {
        clear(ic); int fds[2];
        if(pipe2(fds,O_CLOEXEC)<0) { status(ic,"Cannot create dictation pipe"); return; }
        const std::string python=std::string(SHUANGSHENG_PROJECT_ROOT)+"/.venv/bin/python";
        pid_=fork();
        if(pid_==0) {
            setpgid(0,0); dup2(fds[1],STDOUT_FILENO); close(fds[0]); close(fds[1]);
            // Fcitx may block signals in its main thread. Reset the child's mask.
            sigset_t signals; sigemptyset(&signals); sigprocmask(SIG_SETMASK,&signals,nullptr);
            signal(SIGUSR1,SIG_IGN);
            if(chdir(SHUANGSHENG_PROJECT_ROOT)<0) _exit(126);
            execl(python.c_str(),python.c_str(),"-m","ime.native_voice","--language",language_.c_str(),"--script",traditional_ ? "traditional" : "simplified",static_cast<char *>(nullptr));
            _exit(127);
        }
        close(fds[1]);
        if(pid_<0) { close(fds[0]); status(ic,"Cannot launch dictation"); return; }
        setpgid(pid_,pid_); voiceFd_=fds[0]; fcntl(voiceFd_,F_SETFL,O_NONBLOCK);
        voiceContext_=ic; transcript_.clear(); stopping_=false; cancelledAt_=0; startedAt_=now(CLOCK_MONOTONIC); lastStopSignal_=0;
        status(ic,"Recording… Ctrl+Alt+Space stops · Esc cancels");
    }
    void cancelVoice() {
        if(pid_>0 && !cancelledAt_) { kill(pid_,SIGTERM); cancelledAt_=now(CLOCK_MONOTONIC); }
        voiceContext_=nullptr; transcript_.clear();
    }
    void pollVoice() {
        if(pid_<=0) return;
        auto current=now(CLOCK_MONOTONIC);
        if (stopping_ && !cancelledAt_ && current-startedAt_>300000 && current-lastStopSignal_>500000) { kill(pid_,SIGUSR1); lastStopSignal_=current; }
        if(cancelledAt_ && now(CLOCK_MONOTONIC)-cancelledAt_>2000000) kill(-pid_,SIGKILL);
        char buffer[4096]; ssize_t n;
        while((n=read(voiceFd_,buffer,sizeof(buffer)))>0) {
            if(transcript_.size()+static_cast<size_t>(n)>65536) { cancelVoice(); break; }
            transcript_.append(buffer,n);
        }
        int result=0; auto done=waitpid(pid_,&result,WNOHANG);
        if(done==0 || (done<0 && errno==EINTR)) return;
        // The child can write between the first drain and waitpid. Drain once more
        // after exit so the final bytes are never lost.
        while((n=read(voiceFd_,buffer,sizeof(buffer)))>0) {
            if(transcript_.size()+static_cast<size_t>(n)>65536) { cancelVoice(); break; }
            transcript_.append(buffer,n);
        }
        close(voiceFd_); voiceFd_=-1; pid_=-1;
        auto *ic=voiceContext_; voiceContext_=nullptr;
        if(!ic || !ic->hasFocus() || sensitive(ic)) { transcript_.clear(); return; }
        while(!transcript_.empty() && (transcript_.back()=='\n' || transcript_.back()=='\r')) transcript_.pop_back();
        if(done>0 && WIFEXITED(result) && WEXITSTATUS(result)==0 && !transcript_.empty()) commit(ic,transcript_);
        else status(ic,"Dictation failed or no speech; see Fcitx log");
        transcript_.clear();
    }
    Instance *instance_;
    FactoryFor<State> factory_{[](InputContext &){return new State;}};
    std::vector<Entry> entries_;
    std::vector<std::unique_ptr<HandlerTableEntry<EventHandler>>> handlers_;
    std::unique_ptr<EventSourceTime> timer_;
    bool traditional_=false, stopping_=false;
    std::string language_="zh", transcript_;
    pid_t pid_=-1;
    int voiceFd_=-1;
    uint64_t cancelledAt_=0, startedAt_=0, lastStopSignal_=0;
    InputContext *voiceContext_=nullptr;
};
void Word::select(InputContext *ic) const { engine_->commit(ic,value_); }
class Factory : public AddonFactory {
    AddonInstance *create(AddonManager *manager) override { return new Engine(manager->instance()); }
};
}
FCITX_ADDON_FACTORY_V2(shuangsheng, Factory)
