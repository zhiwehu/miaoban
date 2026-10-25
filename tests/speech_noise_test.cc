#include "../firmware/head/main/boards/echoear/direction_math.h"
#include <cassert>
#include <limits>
int main() {
    echoear::SpeechNoiseFloor floor;
    for (int i = 0; i < 31; ++i) {
        floor.Observe(50, true);
        assert(!floor.Ready());
        assert(!floor.Accepts(85, 150, true));
    }
    floor.Observe(50, true);
    assert(floor.Ready());
    // A permissive VAD may falsely call steady room noise speech; it must still be rejected.
    assert(!floor.Accepts(50, 150, true));
    assert(floor.Accepts(85, 150, true)); // Quiet voice now passes the 1.5x noise margin.
    assert(!floor.Accepts(85, 150, false)); // Keep the independent speech classifier.
    assert(!floor.Accepts(85, 89, true)); // Reject low-amplitude glitches.
    for (int i = 0; i < 100; ++i) floor.Observe(180, true);
    assert(floor.Accepts(85, 150, true)); // Sustained speech must not raise its own gate.
    floor.Observe(std::numeric_limits<double>::quiet_NaN(), false);
    assert(!floor.Accepts(std::numeric_limits<double>::quiet_NaN(), 150, true));

    echoear::SpeechNoiseFloor quiet;
    for (int i = 0; i < 32; ++i) quiet.Observe(20, false);
    assert(!quiet.Accepts(29, 100, true));
    assert(quiet.Accepts(35, 100, true));
}
