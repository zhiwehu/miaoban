#include "../firmware/base_controller/main/motion.h"
#include <cassert>
int main(){
 miaoban::Motion m;assert(m.Request(220,162,0));
 // Far from the goal, sustained movement must not have periodic forced pauses.
 for(int t=20;t<=200;t+=20)assert(m.Tick(162+t*.05f,t)>0);
 // Crossing the target still stops immediately and never reverses.
 assert(m.Tick(222,240)==0 && !m.active);
}
