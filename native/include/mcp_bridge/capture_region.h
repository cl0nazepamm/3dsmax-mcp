#pragma once
#include <nlohmann/json.hpp>
#include <stdexcept>

namespace CaptureRegion {
// Physical pixels. Crop coordinates are relative to the unscaled target.
struct Rect { int x, y, width, height; };
inline Rect Crop(Rect target, const nlohmann::json& crop) {
    if(target.width<=0 || target.height<=0) throw std::runtime_error("Capture target is empty");
    if(crop.is_null()) return target;
    if(crop.type()!=nlohmann::json::value_t::array || crop.size()!=4) throw std::runtime_error("crop must be [x,y,width,height]");
    int values[4];
    for(int i=0;i<4;++i) {
        if(!crop[i].is_number_integer()) throw std::runtime_error("crop must contain integer pixels");
        if(crop[i].is_number_unsigned() && crop[i].get<unsigned long long>()>131072)
            throw std::runtime_error("crop exceeds the supported range");
        auto value=crop[i].get<long long>();
        if(value<0 || value>131072) throw std::runtime_error("crop exceeds the supported range");
        values[i]=static_cast<int>(value);
    }
    if(values[2]<=0 || values[3]<=0 || values[0]+values[2]>target.width || values[1]+values[3]>target.height)
        throw std::runtime_error("crop must lie entirely inside the unscaled capture target");
    return {target.x+values[0],target.y+values[1],values[2],values[3]};
}
inline nlohmann::json Json(const Rect& r) { return {r.x,r.y,r.width,r.height}; }
}
