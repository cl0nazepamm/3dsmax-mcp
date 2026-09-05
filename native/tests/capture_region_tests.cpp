#include "mcp_bridge/capture_region.h"
#include <iostream>
int main() {
    using namespace CaptureRegion;
    using nlohmann::json;
    int failures=0;
    const Rect target{-1800,120,1000,700};
    if(Json(Crop(target,nullptr))!=Json(target)) ++failures;
    if(Json(Crop(target,{10,20,300,200}))!=json({-1790,140,300,200})) ++failures;
    if(Json(Crop(target,{0,0,1000,700}))!=Json(target)) ++failures;
    for(const auto& bad: {json::array(),json({0,0,1001,700}),json({-1,0,10,10}),
            json({0,0,0,10}),json({true,0,10,10}),json({0,0,1.5,10}),json({0,690,10,20}),
            json({0,0,10,18446744073709551615ull})}) {
        try { Crop(target,bad); ++failures; } catch(const std::runtime_error&) {}
    }
    std::cout<<"capture region: "<<failures<<" failures\n";
    return failures ? 1 : 0;
}
