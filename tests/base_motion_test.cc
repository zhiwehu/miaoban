#include "../firmware/base_controller/main/motion.h"
#include <cassert>
#include <limits>
int main() {
    miaoban::Motion m;
    assert(m.Request(400,162,0));assert(m.goal==247);
    assert(m.Tick(162,20)>0); // Short pulse, followed by coast and fresh measurement.
    assert(m.Tick(162,40)>0);
    assert(m.Tick(162,820)==0);assert(m.fault==miaoban::STALL);
    assert(!m.Request(162,162,700)); // A stalled motor cannot be endlessly retried.
    // Gearbox friction can leave a small residual after a successful approach.
    // Settle quietly, rather than latch a fault that disables all later speech turns.
    miaoban::Motion near;
    assert(near.Request(115,130,0));
    assert(near.Tick(118.7f,100)<0);
    assert(near.Tick(118.7f,901)==0);
    assert(!near.active && near.fault==miaoban::OK);
    assert(near.Request(140,118.7f,1000));
    miaoban::Motion blocked;
    assert(blocked.Request(115,121,0));
    assert(blocked.Tick(121,801)==0);
    assert(blocked.fault==miaoban::STALL);
    miaoban::Motion n;
    assert(n.Request(-200,162,0));assert(n.goal==77);
    assert(n.Tick(162,20)<0);assert(n.Tick(162,80)<0);
    n.Tick(std::numeric_limits<float>::quiet_NaN(),60);
    assert(n.fault==miaoban::SENSOR && !n.active);
    miaoban::Motion settled;
    settled.Request(180,162,0);
    assert(settled.Tick(179,300)==0);
    assert(settled.Tick(180,420)==0 && !settled.active);
    miaoban::Motion stopped;
    stopped.Request(190,162,0);
    stopped.Stop(163);
    assert(!stopped.active && stopped.goal==163 && stopped.Tick(163,100)==0);
    assert(stopped.Request(170,163,200));
    miaoban::Motion bounded;
    bounded.Request(180,162,0);
    assert(!bounded.Request(200,164,9900));
    assert(bounded.Tick(165,10001)==0 && bounded.fault==miaoban::TIMEOUT);
}
