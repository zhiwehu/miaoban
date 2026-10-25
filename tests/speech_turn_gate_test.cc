#include "../firmware/head/main/boards/echoear/direction_math.h"
#include <cassert>
int main() {
    echoear::SpeechTurnGate gate;
    // Keep rejecting short transients; require nine stable speech frames (~288ms).
    for (int i = 0; i < 8; ++i) assert(gate.Observe(-25, true, true, i*32) == 0);
    assert(gate.Observe(-25, true, true, 256) == -33);
    assert(gate.Observe(-25, true, true, 288) == 0);
    assert(gate.Observe(-25, true, false, 900) == 0);
    for (int i = 0; i < 8; ++i) assert(gate.Observe(30, true, true, 1000+i*32) == 0);
    assert(gate.Observe(30, true, true, 1256) == 39);

    echoear::SpeechTurnGate center;
    for (int t = 0; t < 1000; t += 32) assert(center.Observe(7, true, true, t) == 0);
    echoear::SpeechTurnGate noisy;
    for (int i = 0; i < 100; ++i) assert(noisy.Observe(i%2 ? 60 : -60, true, true, i*32) == 0);
    echoear::SpeechTurnGate reflected;
    for (int i = 0; i < 8; ++i) assert(reflected.Observe(i==4 ? -10 : 40, true, true, i*32) == 0);
    assert(reflected.Observe(40, true, true, 256) == 52);
    echoear::SpeechTurnGate limit;
    for (int i = 0; i < 8; ++i) assert(limit.Observe(80, true, true, i*32) == 0);
    assert(limit.Observe(80, true, true, 256) == 60);

    // A long pause invalidates the old direction instead of accumulating stale speech forever.
    echoear::SpeechTurnGate paused;
    for (int i = 0; i < 6; ++i) assert(paused.Observe(30, true, true, i*32) == 0);
    for (int i = 0; i < 3; ++i) assert(paused.Observe(0, false, true, 192+i*32) == 0);
    for (int i = 0; i < 8; ++i) assert(paused.Observe(30, true, true, 288+i*32) == 0);
    assert(paused.Observe(30, true, true, 544) == 39);

    // Three-frame noises separated by silence cannot build a movement decision.
    echoear::SpeechTurnGate transients;
    for (int i = 0; i < 100; ++i)
        assert(transients.Observe(30, i%8 < 3, true, i*32) == 0);
}
