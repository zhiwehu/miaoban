#include "../firmware/head/main/boards/echoear/direction_math.h"
#include <cassert>
int main() {
    assert(echoear::DoaDelta(90) == 0);
    assert(echoear::DoaDelta(0) == -90);
    assert(echoear::DoaDelta(180) == 90);
    assert(echoear::DoaDelta(120, true) == -30);
    assert(echoear::RelativeTarget(222, 30, true) == 247);
    assert(echoear::RelativeTarget(222, 30, false) == 192);
    assert(echoear::RelativeTarget(250, 60, true) == 247);
    assert(echoear::RelativeTarget(40, -60, true) == 77);
}
