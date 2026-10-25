#include "../firmware/head/main/boards/echoear/direction_math.h"
#include <cassert>

int main() {
    // Soft speech at RMS 85 should pass a room floor of 50; steady room noise should not.
    echoear::SpeechNoiseFloor room;
    for (int i = 0; i < 32; ++i) room.Observe(50, false);
    assert(room.Threshold() > 50);
    assert(room.Threshold() <= 85);

    // React to a stable direction within 300ms instead of requiring ~480ms.
    echoear::SpeechTurnGate fast;
    int command = 0;
    for (int i = 0; i < 10 && command == 0; ++i)
        command = fast.Observe(30, true, true, i * 32);
    assert(command == 39);

    // Two short unvoiced frames in ordinary speech must not discard prior direction evidence.
    echoear::SpeechTurnGate syllables;
    for (int i = 0; i < 6; ++i) assert(syllables.Observe(30, true, true, i * 32) == 0);
    assert(syllables.Observe(0, false, true, 192) == 0);
    assert(syllables.Observe(0, false, true, 224) == 0);
    assert(syllables.Observe(30, true, true, 256) == 0);
    assert(syllables.Observe(30, true, true, 288) == 0);
    assert(syllables.Observe(30, true, true, 320) == 39);
}
