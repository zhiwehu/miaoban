#pragma once
#include <algorithm>
#include <cmath>
#include <cstdint>
namespace miaoban {
constexpr float kCenter=162;
constexpr float kMin=kCenter-85,kMax=kCenter+85;
enum Fault { OK=0,SENSOR=1,STALL=2,TIMEOUT=3 };
class Motion {
public:
    bool Request(float target,float actual,int64_t now) {
        if (active || fault!=OK || !std::isfinite(target) || !Valid(actual)) return false;
        goal=std::clamp(target,kMin,kMax);
        direction=goal>actual?1:-1;
        active=std::abs(goal-actual)>2;
        started=progress_at=now;progress_angle=actual;
        pulse_until=coast_until=0;cruising=false;
        return true;
    }
    void Stop(float actual) { active=false;if(Valid(actual))goal=actual; }
    int Tick(float actual,int64_t now) {
        if(!Valid(actual)){fault=SENSOR;active=false;return 0;}
        if(!active)return 0;
        // Stop on reaching OR crossing the final goal. A move never reverses.
        const float error=(goal-actual)*direction;
        if(error<=2){active=false;return 0;}
        if(now-started>=10000){fault=TIMEOUT;active=false;return 0;}
        if(std::abs(actual-progress_angle)>=1.2f){progress_at=now;progress_angle=actual;}
        else if(now-progress_at>=800){
            // A quiet final approach may stop a few degrees short due to
            // gearbox friction. Accept that residual, preserving the stall
            // latch for an obstruction farther from the requested position.
            if(error>5)fault=STALL;
            active=false;return 0;
        }
        if((actual<=kMin && direction<0)||(actual>=kMax && direction>0)){
            active=false;return 0;
        }
        // Cruise continuously away from the goal. Only the final approach
        // uses short corrections; never alternate direction inside one move.
        if(error>14){
            cruising=true;pulse_until=coast_until=0;
            return direction*380;
        }
        if(cruising){
            cruising=false;pulse_until=0;coast_until=now+100;
            return 0; // Let the gearbox coast before measuring the final approach.
        }
        if(now<coast_until)return 0;
        if(pulse_until && now>=pulse_until){
            pulse_until=0;coast_until=now+100;return 0;
        }
        if(!pulse_until)pulse_until=now+(error>20?60:(error>5?40:20));
        return direction*380;
    }
    static bool Valid(float a){return std::isfinite(a)&&a>=2&&a<=298;}
    bool active=false;Fault fault=OK;float goal=kCenter;
private:
    int direction=1;bool cruising=false;
    float progress_angle=kCenter;
    int64_t started=0,progress_at=0,pulse_until=0,coast_until=0;
};
}
