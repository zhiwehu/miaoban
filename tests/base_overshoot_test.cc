#include "../firmware/base_controller/main/motion.h"
#include <cassert>
int main(){
    miaoban::Motion m;
    m.Request(167,142.7,0);
    m.Tick(145,20);
    assert(m.Tick(176.8,100)==0);
    assert(!m.active); // Never reverse to chase an overshot target.
    miaoban::Motion n;
    n.Request(140,165,0);
    assert(n.Tick(130,100)==0 && !n.active);
}
