#pragma once
#include <algorithm>
#include <cmath>
#include <array>
#include <cstdint>

namespace echoear {
// The two-microphone array reports 0..180 degrees; broadside is 90.
inline int DoaDelta(float azimuth, bool inverted = false) {
    int delta = static_cast<int>(std::lround(azimuth - 90.0f));
    return inverted ? -delta : delta;
}
inline int RelativeTarget(int current, int delta, bool clockwise_positive) {
    return std::clamp(current + (clockwise_positive ? delta : -delta), 77, 247);
}

// Establish room noise even if a permissive VAD falsely flags it as speech.
class SpeechNoiseFloor {
public:
    void Observe(double rms,bool voice) {
        if(!std::isfinite(rms))return;
        if(count_<samples_.size()) {
            samples_[count_++]=rms;
            if(Ready()) {
                auto sorted=samples_;std::sort(sorted.begin(),sorted.end());
                noise_=std::clamp(sorted[6],20.0,150.0);
            }
        } else if(!voice || rms<noise_*1.25) {
            noise_=.99*noise_+.01*std::clamp(rms,20.0,150.0);
        }
    }
    bool Ready()const{return count_==samples_.size();}
    // VAD already requires speech. Keep a margin above room noise without
    // rejecting a softly spoken voice merely because it is less than 2x louder.
    double Threshold()const{return std::max(30.0,noise_*1.5);}
    bool Accepts(double rms, int peak, bool voice) const {
        return Ready() && voice && std::isfinite(rms) && rms >= Threshold() && peak >= 90;
    }
private:
    std::array<double,32> samples_{};
    size_t count_=0;double noise_=20;
};

// Follow sustained user speech, rather than only a single VAD rising edge.
class SpeechTurnGate {
public:
    int Observe(int delta, bool speech, bool listening, int64_t now_ms) {
        if (!listening) {
            listening_since_ = -1;
            count_ = 0;
            quiet_frames_ = 0;
            return 0;
        }
        if (listening_since_ < 0) listening_since_ = now_ms;
        if (now_ms < next_turn_ms_) {
            count_ = 0;
            quiet_frames_ = 0;
            return 0;
        }
        // Ordinary speech contains short unvoiced syllables and energy dips.
        // They add no direction evidence, but up to 64ms should not erase it.
        if (!speech) {
            if (++quiet_frames_ > 2) count_ = 0;
            return 0;
        }
        quiet_frames_ = 0;
        samples_[index_] = delta;
        index_ = (index_ + 1) % samples_.size();
        if (count_ < samples_.size()) ++count_;
        if (count_ < samples_.size() || now_ms - listening_since_ < 192) return 0;
        auto sorted = samples_;
        std::sort(sorted.begin(), sorted.end());
        // Reject split directions, but tolerate isolated reflection outliers.
        if (sorted[6] - sorted[2] > 15) return 0;
        const int median = sorted[4];
        if (std::abs(median) < 8) return 0;
        // Fixed left/right 45deg trials reported about 25deg / 20..40deg.
        // This conservative first correction needs physical validation; it is
        // not a measured microphone spacing or a change to motor travel limits.
        const int command = std::clamp(static_cast<int>(std::lround(median * 1.3)), -60, 60);
        next_turn_ms_ = now_ms + 350;
        count_ = 0;
        return command;
    }
private:
    std::array<int, 9> samples_{};
    size_t index_ = 0;
    size_t count_ = 0;
    unsigned quiet_frames_ = 0;
    int64_t listening_since_ = -1;
    int64_t next_turn_ms_ = 0;
};

}
