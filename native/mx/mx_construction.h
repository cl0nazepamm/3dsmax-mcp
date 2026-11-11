// Parametric construction shared by curve models (mx_curves.cpp) and lofts
// (mx_solids.cpp), as the curve_model and loft_mesh tools share
// maxmcp/tools/modeling/_loft.py: named parameters, coordinate expressions over them,
// quad cages between matched sections, and the cage fingerprint both tools
// record in AppData. Each follows that Python exactly, so a model made by
// either side stays updatable from both.
#pragma once

#include "mx_common.h"

#include <array>
#include <map>

class MNMesh;

// Parameter values by name, as curve models and lofts take them (ParseValues
// in mx_curves.cpp, ParamsFromPython in mx_solids.cpp): {'width': 40}, at most
// 64 numbers (a bool is not one) under names IsParameterName accepts. Declared
// with .none() where leaving it out means no parameters.
MX_ARG_TYPE(ParametersArg, "dict[str, float]")

namespace mx {
inline namespace MX_ABI_TAG {

// Parameter values by name.
using Values = std::map<std::string, double>;

// `value`, unless it is not finite or its magnitude exceeds 1e12.
double Finite(double value, const std::string& label);
// Python's float %: the result takes the divisor's sign.
double PyMod(double a, double b);

extern const char* const kParameterNameRule;
// An ASCII identifier of 1-64 characters that is not pi or a math function.
bool IsParameterName(const std::string& name);

// A coordinate expression over the parameters ("-width/2"): Python syntax for
// numbers, names, pi, + - * / % ** and sin cos sqrt abs min max, computed as
// Python computes it.
double Expression(const std::string& text, const Values& values);

// Quads between matched sections of points, 0-based, as build_loft makes
// them: caps close an open path's ends, reverse flips every face.
struct LoftShape {
    bool profileClosed = true;
    bool closePath = false;
    bool caps = false;
    bool reverse = false;
};

void CheckLoftCounts(size_t sections, size_t perSection, const LoftShape& shape);
std::vector<std::vector<int>> LoftFaces(size_t sections, size_t perSection, const LoftShape& shape);

// build_loft's checks on evaluated sections, then LoftFaces. `Point` is any
// type with == and [axis].
template <typename Point>
std::vector<std::vector<int>> LoftQuads(const std::vector<std::vector<Point>>& sections, const LoftShape& shape) {
    CheckLoftCounts(sections.size(), sections.empty() ? 0 : sections[0].size(), shape);
    const size_t per = sections[0].size();
    for (size_t s = 0; s < sections.size(); ++s) {
        const auto& section = sections[s];
        if (section.size() != per) throw BadParam("All sections must contain the same number of corresponding points");
        for (const Point& p : section)
            for (int axis = 0; axis < 3; ++axis) Finite(p[axis], "section coordinate");
        if (shape.profileClosed && section.front() == section.back())
            throw BadParam("Closed profiles wrap automatically; do not repeat the first point at the end");
        for (size_t j = 0; j < (shape.profileClosed ? per : per - 1); ++j)
            if (section[j] == section[(j + 1) % per]) throw BadParam("A section contains a zero-length segment");
        if (s > 0 && section == sections[s - 1]) throw BadParam("Consecutive sections must not be identical");
    }
    if (shape.closePath && sections.front() == sections.back())
        throw BadParam("Closed paths wrap automatically; do not repeat the first section");
    return LoftFaces(sections.size(), per, shape);
}

// ── Fingerprints ─────────────────────────────────────────────────

using Digest = std::array<unsigned char, 32>;
Digest Sha256(const std::string& data);
// Lowercase hex, as Python's hexdigest().
std::string HexDigest(const Digest& digest);
// "AB-CD-...", as .NET's BitConverter.ToString, which the tools' MAXScript uses.
std::string DashedDigest(const Digest& digest);
bool IsFingerprint(const std::string& text);

// formattedPrint v format:".9g", whatever the C locale.
std::string G9(float v);
// The tools' lfToken: a dashed SHA-256 of an Editable Poly cage's counts,
// dead components, vertex positions, edges and faces.
std::string CageFingerprint(const MNMesh& mesh);

// MAXScript AppData text on a node (getAppData / setAppData: their chunk ids
// are not part of the SDK). Set reads the text back: MaxError naming `what`
// unless it stuck.
std::optional<std::string> GetAppData(INode* node, int slot);
void SetAppData(INode* node, int slot, const std::string& text, const std::string& what);

} // namespace MX_ABI_TAG
} // namespace mx
