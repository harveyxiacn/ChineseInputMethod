// SPDX-License-Identifier: MIT
#include "paths.h"
#include "pinyin.h"
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
struct State : InputContextProperty {
    explicit State(libime::PinyinIME *ime) : context(ime) {}
    libime::PinyinContext context;
};
class Engine;
class Word : public CandidateWord {
public:
    Word(std::string text, size_t index, Engine *engine) : CandidateWord(Text(text)), index_(index), engine_(engine) {}
    void select(InputContext *ic) const override;
private:
    size_t index_;
    Engine *engine_;
};
class Engine : public InputMethodEngine {
public:
    explicit Engine(Instance *instance) : instance_(instance) {
        instance_->inputContextManager().registerProperty("shuangsheng", &factory_);
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
        if (errorFd_ >= 0) close(errorFd_);
    }
    void reset(const InputMethodEntry &, InputContextEvent &event) override {
        if (voiceContext_ == event.inputContext()) cancelVoice();
        clear(event.inputContext());
    }
    void commit(InputContext *ic, const std::string &text) {
        // Candidate selection may destroy the CandidateWord; copy before clearing UI.
        auto value=text; clear(ic); if (ic->hasFocus() && !sensitive(ic)) ic->commitString(value);
    }
    void select(InputContext *ic, size_t index) {
        if (!ic->hasFocus() || sensitive(ic)) { clear(ic); return; }
        auto &context=ic->propertyFor(&factory_)->context;
        if (index>=context.candidates().size()) return;
        context.select(index);
        if (context.selected()) {
            auto text=pinyin_.display(context.selectedSentence(), traditional_);
            context.learn(); pinyin_.save(); commit(ic,text);
        } else update(ic);
    }
    void keyEvent(const InputMethodEntry &, KeyEvent &event) override {
        if (event.isRelease()) return;
        auto *ic=event.inputContext();
        if (sensitive(ic)) { if (voiceContext_==ic) cancelVoice(); clear(ic); return; }
        auto key=event.key(); auto &context=ic->propertyFor(&factory_)->context;
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
            if (voiceContext_==ic || !context.empty()) { cancelVoice(); clear(ic); event.filterAndAccept(); }
            return;
        }
        if (voiceContext_==ic) return;
        if (key.states() & (KeyStates(KeyState::Ctrl) | KeyState::Alt | KeyState::Super)) return;
        auto sym=key.sym();
        if ((sym>=FcitxKey_a && sym<=FcitxKey_z) || (sym==FcitxKey_apostrophe && !context.empty())) {
            if (context.size()<128) context.type(std::string(1,static_cast<char>(sym)));
            update(ic); event.filterAndAccept(); return;
        }
        if (context.empty()) return;
        if (sym==FcitxKey_Left || sym==FcitxKey_Right || sym==FcitxKey_Home || sym==FcitxKey_End) {
            if (sym==FcitxKey_Left && context.cursor()>0) context.setCursor(context.cursor()-1);
            if (sym==FcitxKey_Right && context.cursor()<context.size()) context.setCursor(context.cursor()+1);
            if (sym==FcitxKey_Home) context.setCursor(context.selectedLength());
            if (sym==FcitxKey_End) context.setCursor(context.size());
            update(ic); event.filterAndAccept(); return;
        }
        if (sym==FcitxKey_Delete) {
            if(context.cursor()<context.size()) context.erase(context.cursor(),context.cursor()+1);
            update(ic); event.filterAndAccept(); return;
        }
        auto list=ic->inputPanel().candidateList();
        if (sym==FcitxKey_BackSpace) { if(context.cursor()<=context.selectedLength()) context.cancel(); else context.backspace(); update(ic); event.filterAndAccept(); return; }
        if (sym==FcitxKey_Return || sym==FcitxKey_KP_Enter) { commit(ic,context.userInput()); event.filterAndAccept(); return; }
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
        if (sym==FcitxKey_space) { commit(ic,context.userInput()); event.filterAndAccept(); return; }
        // Commit composition before ordinary punctuation, then let the app handle it.
        if (sym>=FcitxKey_exclam && sym<=FcitxKey_asciitilde) {
            if(list && !list->empty()) {
                // Complete any remaining segments before forwarding punctuation.
                while (!context.empty() && !context.candidates().empty()) {
                    context.select(0);
                    if(context.selected()) {
                        auto text=pinyin_.display(context.selectedSentence(),traditional_);
                        context.learn(); pinyin_.save(); commit(ic,text); break;
                    }
                }
            } else commit(ic,context.userInput());
        }
    }
private:
    static bool sensitive(InputContext *ic) {
        return bool(ic->capabilityFlags() & (CapabilityFlags(CapabilityFlag::Password) | CapabilityFlag::Sensitive));
    }
    void clear(InputContext *ic) {
        ic->propertyFor(&factory_)->context.clear(); ic->inputPanel().reset();
        ic->updatePreedit(); ic->updateUserInterface(UserInterfaceComponent::InputPanel);
    }
    void status(InputContext *ic, const std::string &text) {
        ic->inputPanel().setAuxUp(Text(text + (traditional_ ? " · 繁" : " · 简")));
        ic->updateUserInterface(UserInterfaceComponent::InputPanel);
    }
    void update(InputContext *ic) {
        auto &context=ic->propertyFor(&factory_)->context;
        auto [text,cursor]=context.preeditWithCursor();
        // Convert selected Hanzi as well as candidates. Cursor is a UTF-8 byte offset.
        auto before=pinyin_.display(text.substr(0,cursor),traditional_);
        auto after=pinyin_.display(text.substr(cursor),traditional_);
        ic->inputPanel().reset(); Text preedit(before+after); preedit.setCursor(before.size());
        ic->inputPanel().setClientPreedit(preedit); ic->inputPanel().setPreedit(preedit);
        auto list=std::make_unique<CommonCandidateList>(); list->setPageSize(9);
        list->setSelectionKey({Key(FcitxKey_1),Key(FcitxKey_2),Key(FcitxKey_3),Key(FcitxKey_4),Key(FcitxKey_5),Key(FcitxKey_6),Key(FcitxKey_7),Key(FcitxKey_8),Key(FcitxKey_9)});
        size_t index=0;
        for (const auto &candidate:context.candidates()) {
            list->append(std::make_unique<Word>(pinyin_.display(candidate.toString(),traditional_),index++,this));
        }
        if (!list->empty()) list->setGlobalCursorIndex(0);
        ic->inputPanel().setCandidateList(std::move(list));
        if (!context.empty()) status(ic,language_=="yue" ? "粤语 · Ctrl+Alt+Space 录音" : "普通话 · Ctrl+Alt+Space 录音");
        ic->updatePreedit(); ic->updateUserInterface(UserInterfaceComponent::InputPanel);
    }
    void startVoice(InputContext *ic) {
        clear(ic); int fds[2], errors[2];
        if(pipe2(fds,O_CLOEXEC)<0) { status(ic,"Cannot create dictation pipe"); return; }
        if(pipe2(errors,O_CLOEXEC)<0) { close(fds[0]); close(fds[1]); status(ic,"Cannot create dictation error pipe"); return; }
        const std::string python=std::string(SHUANGSHENG_PROJECT_ROOT)+"/.venv/bin/python";
        pid_=fork();
        if(pid_==0) {
            setpgid(0,0); dup2(fds[1],STDOUT_FILENO); close(fds[0]); close(fds[1]);
            dup2(errors[1],STDERR_FILENO); close(errors[0]); close(errors[1]);
            // Fcitx may block signals in its main thread. Reset the child's mask.
            sigset_t signals; sigemptyset(&signals); sigprocmask(SIG_SETMASK,&signals,nullptr);
            signal(SIGUSR1,SIG_IGN);
            if(chdir(SHUANGSHENG_PROJECT_ROOT)<0) { dprintf(STDERR_FILENO,"Project directory missing. Reinstall Shuangsheng.\n"); _exit(126); }
            execl(python.c_str(),python.c_str(),"-m","ime.native_voice","--protocol","--language",language_.c_str(),"--script",traditional_ ? "traditional" : "simplified",static_cast<char *>(nullptr));
            dprintf(STDERR_FILENO,"Cannot start speech Python. Install the project speech requirements.\n"); _exit(127);
        }
        close(fds[1]); close(errors[1]);
        if(pid_<0) { close(fds[0]); close(errors[0]); status(ic,"Cannot launch dictation"); return; }
        setpgid(pid_,pid_); voiceFd_=fds[0]; fcntl(voiceFd_,F_SETFL,O_NONBLOCK);
        errorFd_=errors[0]; fcntl(errorFd_,F_SETFL,O_NONBLOCK); voiceError_.clear();
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
        if (!stopping_ && voiceContext_ && current-startedAt_>115000000) {
            stopping_=true; status(voiceContext_,"Transcribing… Esc cancels");
        }
        if (stopping_ && !cancelledAt_ && current-startedAt_>300000 && current-lastStopSignal_>500000) { kill(pid_,SIGUSR1); lastStopSignal_=current; }
        if(cancelledAt_ && now(CLOCK_MONOTONIC)-cancelledAt_>2000000) kill(-pid_,SIGKILL);
        char buffer[4096]; ssize_t n;
        auto drainErrors=[this,&buffer,&n]() {
            while ((n=read(errorFd_,buffer,sizeof(buffer)))>0) {
                voiceError_.append(buffer,n);
                if (voiceError_.size()>4096) voiceError_.erase(0,voiceError_.size()-4096);
            }
        };
        drainErrors();
        while((n=read(voiceFd_,buffer,sizeof(buffer)))>0) {
            if(transcript_.size()+static_cast<size_t>(n)>65536) { cancelVoice(); break; }
            transcript_.append(buffer,n);
        }
        int result=0; auto done=waitpid(pid_,&result,WNOHANG);
        if(done==0 || (done<0 && errno==EINTR)) return;
        // Fcitx's SIGCHLD zombie reaper can collect addon children before this
        // timer. ECHILD is completion, not failure; require the worker's complete
        // success frame instead of relying solely on a waitpid exit status.
        bool reapedByHost = done<0 && errno==ECHILD;
        // The child can write between the first drain and waitpid. Drain once more
        // after exit so the final bytes are never lost.
        while((n=read(voiceFd_,buffer,sizeof(buffer)))>0) {
            if(transcript_.size()+static_cast<size_t>(n)>65536) { cancelVoice(); break; }
            transcript_.append(buffer,n);
        }
        drainErrors(); close(errorFd_); errorFd_=-1;
        close(voiceFd_); voiceFd_=-1; pid_=-1;
        auto *ic=voiceContext_; voiceContext_=nullptr;
        if(!ic || !ic->hasFocus() || sensitive(ic)) { transcript_.clear(); return; }
        while(!transcript_.empty() && (transcript_.back()=='\n' || transcript_.back()=='\r')) transcript_.pop_back();
        const std::string prefix="SHUANGSHENG_OK\n", suffix="\nSHUANGSHENG_END";
        bool framed=transcript_.size()>prefix.size()+suffix.size() && transcript_.compare(0,prefix.size(),prefix)==0 &&
            transcript_.compare(transcript_.size()-suffix.size(),suffix.size(),suffix)==0;
        if((reapedByHost || (done>0 && WIFEXITED(result) && WEXITSTATUS(result)==0)) && framed)
            commit(ic,transcript_.substr(prefix.size(),transcript_.size()-prefix.size()-suffix.size()));
        else {
            while (!voiceError_.empty() && (voiceError_.back()=='\n' || voiceError_.back()=='\r')) voiceError_.pop_back();
            auto line=voiceError_.substr(voiceError_.find_last_of('\n')==std::string::npos ? 0 : voiceError_.find_last_of('\n')+1);
            std::string readable;
            for (unsigned char c:line) if (c>=32 && c<127 && readable.size()<500) readable+=c;
            status(ic,readable.empty() ? "Dictation failed or no speech. Check microphone and speech setup." : readable);
        }
        transcript_.clear();
    }
    Instance *instance_;
    shuangsheng::Pinyin pinyin_;
    FactoryFor<State> factory_{[this](InputContext &){return new State(pinyin_.ime());}};
    std::vector<std::unique_ptr<HandlerTableEntry<EventHandler>>> handlers_;
    std::unique_ptr<EventSourceTime> timer_;
    bool traditional_=false, stopping_=false;
    std::string language_="zh", transcript_, voiceError_;
    pid_t pid_=-1;
    int voiceFd_=-1, errorFd_=-1;
    uint64_t cancelledAt_=0, startedAt_=0, lastStopSignal_=0;
    InputContext *voiceContext_=nullptr;
};
void Word::select(InputContext *ic) const { engine_->select(ic,index_); }
class Factory : public AddonFactory {
    AddonInstance *create(AddonManager *manager) override { return new Engine(manager->instance()); }
};
}
FCITX_ADDON_FACTORY_V2(shuangsheng, Factory)
