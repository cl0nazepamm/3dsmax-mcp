#pragma once
#include <nlohmann/json.hpp>
#include <stdexcept>
#include <algorithm>
#include <cwctype>
#include <string>

namespace CaptureRegion {
inline bool MatchesVfb(const std::string& target, std::wstring title, std::wstring windowClass) {
    const auto lower=[](wchar_t c) { return std::towlower(c); };
    std::transform(title.begin(),title.end(),title.begin(),lower);
    std::transform(windowClass.begin(),windowClass.end(),windowClass.begin(),lower);
    if(target=="fstorm_vfb") {
        // FStorm 2.0 RT uses a native dialog with live sample/size metadata.
        // Exclude Render Setup, material dialogs and similarly named scenes.
        return windowClass==L"#32770" && title.rfind(L"fstorm rt: samples ",0)==0 &&
            title.find(L",  resolution ")!=std::wstring::npos && title.find(L",  zoom ")!=std::wstring::npos;
    }
    if(target=="tyflow_editor") {
        // "<flow name> : [tyFlow PRO v1.234 Editor]", a native dialog.
        return windowClass==L"#32770" && title.find(L" : [tyflow ")!=std::wstring::npos &&
            title.size()>=8 && title.compare(title.size()-8,8,L" editor]")==0;
    }
    if(target=="corona_vfb") {
        // VFB 2 has a product/version caption rather than the text "VFB".
        // Require its renderer-owned Qt class and frame metadata so a Corona
        // toolbar, settings dialog, or .max filename cannot become the target.
        const bool modern=title.rfind(L"corona ",0)==0 &&
            windowClass.find(L"legionqtqwindow")!=std::wstring::npos &&
            title.find(L" | camera:")!=std::wstring::npos && title.find(L" | frame ")!=std::wstring::npos;
        return modern || title==L"corona vfb" || title.rfind(L"corona vfb |",0)==0;
    }
    return target=="vray_vfb" && (title.find(L"v-ray frame buffer")!=std::wstring::npos ||
        title.find(L"v-ray virtual frame buffer")!=std::wstring::npos);
}
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
