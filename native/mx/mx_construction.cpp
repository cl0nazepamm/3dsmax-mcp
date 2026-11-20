// Parametric construction shared by curve models and lofts (mx_construction.h).
#include "mx_construction.h"

#include <bcrypt.h>
#include <mnmesh.h>

#include <charconv>
#include <functional>
#include <locale.h>
#include <set>

#pragma comment(lib, "bcrypt.lib")

namespace mx {
inline namespace MX_ABI_TAG {

double Finite(double value, const std::string& label) {
    if (!std::isfinite(value) || std::fabs(value) > 1e12)
        throw BadParam(label + " must be a finite number with magnitude at most 1e12");
    return value;
}

double PyMod(double a, double b) {
    double r = std::fmod(a, b);
    if (r != 0 && ((r < 0) != (b < 0))) r += b;
    return r == 0 ? std::copysign(0.0, b) : r;
}

static const char* const kMathFunctions[] = {"sin", "cos", "sqrt", "abs", "min", "max"};

static bool IsMathName(const std::string& name) {
    return name == "pi" || std::any_of(std::begin(kMathFunctions), std::end(kMathFunctions),
                                       [&](const char* f) { return name == f; });
}

const char* const kParameterNameRule =
    "Parameter names must be ASCII identifiers and cannot shadow pi or math functions";

bool IsParameterName(const std::string& name) {
    auto alpha = [](char c) { return (c >= 'a' && c <= 'z') || (c >= 'A' && c <= 'Z'); };
    if (name.empty() || name.size() > 64 || !alpha(name[0]) || IsMathName(name)) return false;
    return std::all_of(name.begin(), name.end(), [&](char c) { return alpha(c) || (c >= '0' && c <= '9') || c == '_'; });
}

// ── Expressions ──────────────────────────────────────────────────
// loft.py parses with Python's ast and walks the tree, so this parses the
// same subset of Python's syntax to a tree first: its node count (as
// ast.walk counts it) is checked before anything is computed.

namespace {

struct ExprNode {
    char kind = 'n';  // n number, v name, u unary, b binary, c call
    char op = 0;      // + - * / % ^ (for **) and f (for //, which Python parses but loft.py refuses)
    double value = 0;
    std::string name;
    std::vector<ExprNode> args;
};

struct ExprSyntax {};

class ExprParser {
public:
    explicit ExprParser(const std::string& text) : text_(text) {}

    ExprNode Parse() {
        ExprNode e = Sum();
        Space();
        if (at_ != text_.size()) throw ExprSyntax{};
        return e;
    }

private:
    const std::string& text_;
    size_t at_ = 0;

    void Space() {
        while (at_ < text_.size() && (text_[at_] == ' ' || text_[at_] == '\t')) ++at_;
    }
    bool Next(const char* token) {
        Space();
        const size_t n = std::strlen(token);
        if (text_.compare(at_, n, token) != 0) return false;
        at_ += n;
        return true;
    }
    static ExprNode Make(char kind, char op, std::vector<ExprNode> args) {
        ExprNode e;
        e.kind = kind;
        e.op = op;
        e.args = std::move(args);
        return e;
    }
    ExprNode Sum() {
        ExprNode left = Product();
        for (;;) {
            const char op = Next("+") ? '+' : Next("-") ? '-' : 0;
            if (!op) return left;
            ExprNode right = Product();
            left = Make('b', op, {std::move(left), std::move(right)});
        }
    }
    ExprNode Product() {
        ExprNode left = Unary();
        for (;;) {
            const char op = Next("//") ? 'f' : Next("*") ? '*' : Next("/") ? '/' : Next("%") ? '%' : 0;
            if (!op) return left;
            ExprNode right = Unary();
            left = Make('b', op, {std::move(left), std::move(right)});
        }
    }
    ExprNode Unary() {
        if (Next("-")) return Make('u', '-', {Unary()});
        if (Next("+")) return Make('u', '+', {Unary()});
        return Power();
    }
    // Right-associative and tighter than unary minus on its left: -2**2 == -4.
    ExprNode Power() {
        ExprNode base = Primary();
        if (!Next("**")) return base;
        ExprNode exponent = Unary();
        return Make('b', '^', {std::move(base), std::move(exponent)});
    }
    ExprNode Primary() {
        Space();
        if (at_ >= text_.size()) throw ExprSyntax{};
        const unsigned char c = text_[at_];
        if (c == '(') {
            ++at_;
            ExprNode inner = Sum();
            if (!Next(")")) throw ExprSyntax{};
            return inner;
        }
        if (std::isdigit(c) || c == '.') return NumberLiteral();
        if (!IsAsciiAlpha(c) && c != '_') throw ExprSyntax{};
        ExprNode e;
        e.name = Identifier();
        e.kind = 'v';
        if (!Next("(")) return e;
        e.kind = 'c';
        do e.args.push_back(Sum()); while (Next(","));
        if (!Next(")")) throw ExprSyntax{};
        return e;
    }
    static bool IsAsciiAlpha(unsigned char c) { return (c >= 'a' && c <= 'z') || (c >= 'A' && c <= 'Z'); }
    static bool IsWordChar(unsigned char c) { return IsAsciiAlpha(c) || std::isdigit(c) || c == '_'; }
    std::string Identifier() {
        static const std::set<std::string> keywords = {
            "False", "None", "True", "and", "as", "assert", "async", "await", "break", "class", "continue",
            "def", "del", "elif", "else", "except", "finally", "for", "from", "global", "if", "import", "in",
            "is", "lambda", "nonlocal", "not", "or", "pass", "raise", "return", "try", "while", "with", "yield"};
        const size_t start = at_;
        while (at_ < text_.size() && IsWordChar(text_[at_])) ++at_;
        if (at_ < text_.size() && (unsigned char)text_[at_] >= 0x80) throw ExprSyntax{};
        std::string word = text_.substr(start, at_ - start);
        if (keywords.count(word)) throw ExprSyntax{};
        return word;
    }
    // Python decimal literals: 12, 1.5, .5, 5., 1e-3. Leading zeros only in floats.
    ExprNode NumberLiteral() {
        const size_t start = at_;
        auto digits = [&] {
            const size_t from = at_;
            while (at_ < text_.size() && std::isdigit((unsigned char)text_[at_])) ++at_;
            return at_ - from;
        };
        const size_t whole = digits();
        bool isFloat = false;
        size_t fraction = 0;
        if (at_ < text_.size() && text_[at_] == '.') {
            ++at_;
            isFloat = true;
            fraction = digits();
        }
        if (!whole && !fraction) throw ExprSyntax{};
        bool negativeExponent = false;
        if (at_ < text_.size() && (text_[at_] == 'e' || text_[at_] == 'E')) {
            ++at_;
            if (at_ < text_.size() && (text_[at_] == '+' || text_[at_] == '-')) negativeExponent = text_[at_++] == '-';
            if (!digits()) throw ExprSyntax{};
            isFloat = true;
        }
        if (at_ < text_.size() && (IsWordChar(text_[at_]) || text_[at_] == '.' || (unsigned char)text_[at_] >= 0x80))
            throw ExprSyntax{};
        if (!isFloat && whole > 1 && text_[start] == '0' && text_.find_first_not_of('0', start) < at_) throw ExprSyntax{};
        ExprNode e;
        // Out of range, Python gives inf (refused later) or, below the smallest double, 0.
        if (std::from_chars(text_.data() + start, text_.data() + at_, e.value).ec == std::errc::result_out_of_range)
            e.value = negativeExponent ? 0.0 : HUGE_VAL;
        return e;
    }
};

// Nodes as Python's ast.walk counts them (each operator and name context is a node).
int CountNodes(const ExprNode& e) {
    int n = e.kind == 'n' ? 1 : e.kind == 'c' ? 3 : 2;
    for (const ExprNode& arg : e.args) n += CountNodes(arg);
    return n;
}

[[noreturn]] void CannotEvaluate(const std::string& text, const char* reason) {
    throw BadParam("Cannot evaluate coordinate expression '" + text + "': " + reason);
}

double Evaluate(const ExprNode& e, const Values& values, const std::string& text);

double CallFunction(const ExprNode& e, const Values& values, const std::string& text) {
    if (e.name == "pi" || !IsMathName(e.name))
        throw BadParam("Only sin, cos, sqrt, abs, min and max calls without keywords are allowed");
    const bool minmax = e.name == "min" || e.name == "max";
    if (minmax ? e.args.size() < 2 || e.args.size() > 8 : e.args.size() != 1)
        throw BadParam("Math calls need one argument, or 2-8 for min/max");
    std::vector<double> xs;
    for (const ExprNode& arg : e.args) xs.push_back(Evaluate(arg, values, text));
    double out = 0;
    if (e.name == "sin") out = std::sin(xs[0]);
    else if (e.name == "cos") out = std::cos(xs[0]);
    else if (e.name == "abs") out = std::fabs(xs[0]);
    else if (e.name == "min") out = *std::min_element(xs.begin(), xs.end());
    else if (e.name == "max") out = *std::max_element(xs.begin(), xs.end());
    else if (xs[0] < 0) CannotEvaluate(text, "math domain error");
    else out = std::sqrt(xs[0]);
    return Finite(out, "function result");
}

double Evaluate(const ExprNode& e, const Values& values, const std::string& text) {
    switch (e.kind) {
    case 'n': return Finite(e.value, "expression constant");
    case 'v': {
        if (e.name == "pi") return 3.141592653589793;
        const auto found = values.find(e.name);
        if (found == values.end()) {
            std::vector<std::string> names;
            for (const auto& entry : values) names.push_back(entry.first);
            throw BadParam("Unknown construction parameter: " + e.name + "." + Suggest(e.name, names));
        }
        return found->second;
    }
    case 'u': {
        const double v = Evaluate(e.args[0], values, text);
        return e.op == '-' ? -v : v;
    }
    case 'c': return CallFunction(e, values, text);
    default: break;
    }
    const double a = Evaluate(e.args[0], values, text), b = Evaluate(e.args[1], values, text);
    double out = 0;
    switch (e.op) {
    case '+': out = a + b; break;
    case '-': out = a - b; break;
    case '*': out = a * b; break;
    case '/': if (b == 0) CannotEvaluate(text, "division by zero"); out = a / b; break;
    case '%': if (b == 0) CannotEvaluate(text, "modulo by zero"); out = PyMod(a, b); break;
    case '^':
        if (std::fabs(b) > 16) throw BadParam("Expression exponents must have magnitude at most 16");
        if (a == 0 && b < 0) CannotEvaluate(text, "0.0 cannot be raised to a negative power");
        out = std::pow(a, b);  // a negative base with a fractional exponent: NaN, refused as Python's complex is
        break;
    default: throw BadParam("Only +, -, *, /, %, ** arithmetic is supported");
    }
    return Finite(out, "expression result");
}

} // namespace

double Expression(const std::string& text, const Values& values) {
    if (text.empty() || text.size() > 256) throw BadParam("Coordinate expressions must contain 1-256 characters");
    ExprNode tree;
    try {
        tree = ExprParser(Trim(text)).Parse();
    } catch (const ExprSyntax&) {
        throw BadParam("Invalid coordinate expression: '" + text + "'");
    }
    if (1 + CountNodes(tree) > 128) throw BadParam("Coordinate expression is too complex");
    return Evaluate(tree, values, text);
}

// ── Lofts ────────────────────────────────────────────────────────

void CheckLoftCounts(size_t sections, size_t perSection, const LoftShape& shape) {
    if (shape.caps && (!shape.profileClosed || shape.closePath)) throw BadParam("caps requires closed profiles and an open path");
    const size_t minSections = shape.closePath ? 3 : 2;
    if (sections < minSections || sections > 1000)
        throw BadParam("Use " + std::to_string(minSections) + "-1000 ordered cross sections");
    const size_t minPoints = shape.profileClosed ? 3 : 2;
    if (perSection < minPoints || perSection > 1000)
        throw BadParam("Each section needs " + std::to_string(minPoints) + "-1000 points");
    if (sections * perSection > 50000) throw BadParam("Use at most 50000 loft vertices");
}

std::vector<std::vector<int>> LoftFaces(size_t sections, size_t perSection, const LoftShape& shape) {
    const int S = int(sections), P = int(perSection);
    std::vector<std::vector<int>> faces;
    for (int si = 0; si < (shape.closePath ? S : S - 1); ++si) {
        const int next = (si + 1) % S;
        for (int j = 0; j < (shape.profileClosed ? P : P - 1); ++j) {
            const int k = (j + 1) % P;
            faces.push_back({si * P + j, si * P + k, next * P + k, next * P + j});
        }
    }
    if (shape.caps) {
        std::vector<int> first, last;
        for (int j = P - 1; j >= 0; --j) first.push_back(j);
        for (int j = 0; j < P; ++j) last.push_back((S - 1) * P + j);
        faces.push_back(first);
        faces.push_back(last);
    }
    if (shape.reverse)
        for (auto& face : faces) std::reverse(face.begin(), face.end());
    if (faces.size() > 50000) throw BadParam("Use at most 50000 loft faces");
    return faces;
}

// ── Fingerprints ─────────────────────────────────────────────────

Digest Sha256(const std::string& data) {
    static BCRYPT_ALG_HANDLE algorithm = [] {
        BCRYPT_ALG_HANDLE handle = nullptr;
        return BCRYPT_SUCCESS(BCryptOpenAlgorithmProvider(&handle, BCRYPT_SHA256_ALGORITHM, nullptr, 0)) ? handle : nullptr;
    }();
    Digest digest{};
    BCRYPT_HASH_HANDLE hash = nullptr;
    const bool ok = algorithm && BCRYPT_SUCCESS(BCryptCreateHash(algorithm, &hash, nullptr, 0, nullptr, 0, 0)) &&
                    BCRYPT_SUCCESS(BCryptHashData(hash, (PUCHAR)data.data(), ULONG(data.size()), 0)) &&
                    BCRYPT_SUCCESS(BCryptFinishHash(hash, digest.data(), ULONG(digest.size()), 0));
    if (hash) BCryptDestroyHash(hash);
    if (!ok) throw MaxError("SHA-256 is unavailable on this system");
    return digest;
}

std::string HexDigest(const Digest& digest) {
    static const char* digits = "0123456789abcdef";
    std::string out;
    for (unsigned char b : digest) { out += digits[b >> 4]; out += digits[b & 15]; }
    return out;
}

std::string DashedDigest(const Digest& digest) {
    static const char* digits = "0123456789ABCDEF";
    std::string out;
    for (unsigned char b : digest) {
        if (!out.empty()) out += '-';
        out += digits[b >> 4];
        out += digits[b & 15];
    }
    return out;
}

bool IsFingerprint(const std::string& text) {
    if (text.size() != 95) return false;
    for (size_t i = 0; i < text.size(); ++i) {
        const char c = text[i];
        if (i % 3 == 2 ? c != '-' : !(std::isdigit((unsigned char)c) || (c >= 'A' && c <= 'F'))) return false;
    }
    return true;
}

std::string G9(float v) {
    static _locale_t c = _create_locale(LC_NUMERIC, "C");
    char text[32];
    _snprintf_l(text, sizeof text, "%.9g", c, double(v));
    return text;
}

// MAXScript's print form of a BitArray: #{1..3, 5}.
static std::string Bits(int count, const std::function<bool(int)>& set) {
    std::string out;
    for (int i = 0; i < count; ++i) {
        if (!set(i)) continue;
        int j = i;
        while (j + 1 < count && set(j + 1)) ++j;
        out += (out.empty() ? "" : ", ") + std::to_string(i + 1) + (j > i ? ".." + std::to_string(j + 1) : "");
        i = j;
    }
    return "#{" + out + "}";
}

// MAXScript's print form of an array of 1-based IDs: #(1, 2).
static std::string IdList(const int* ids, int count) {
    std::string out = "#(";
    for (int i = 0; i < count; ++i) out += (i ? ", " : "") + std::to_string(ids[i] + 1);
    return out + ")";
}

std::string CageFingerprint(const MNMesh& mesh) {
    const int nv = mesh.VNum(), ne = mesh.ENum(), nf = mesh.FNum();
    std::string out = std::to_string(nv) + "," + std::to_string(ne) + "," + std::to_string(nf) + "|";
    out += Bits(nv, [&](int i) { return mesh.V(i)->GetFlag(MN_DEAD) != 0; }) + "|";
    out += Bits(ne, [&](int i) { return mesh.E(i)->GetFlag(MN_DEAD) != 0; }) + "|";
    out += Bits(nf, [&](int i) { return mesh.F(i)->GetFlag(MN_DEAD) != 0; }) + "|";
    for (int i = 0; i < nv; ++i) {
        const Point3& p = mesh.V(i)->p;
        out += G9(p.x) + ";" + G9(p.y) + ";" + G9(p.z) + ";";
    }
    for (int i = 0; i < ne; ++i) {
        const int ids[] = {mesh.E(i)->v1, mesh.E(i)->v2};
        out += "E" + IdList(ids, 2) + ";";
    }
    for (int i = 0; i < nf; ++i) out += "F" + IdList(mesh.F(i)->vtx, mesh.F(i)->deg) + ";";
    return DashedDigest(Sha256(out));
}

// ── AppData ──────────────────────────────────────────────────────

std::optional<std::string> GetAppData(INode* node, int slot) {
    ScopedMaxScriptEvaluationContext context;
    three_value_locals(target, id, result);
    vl.target = MAXNode::intern(node);
    vl.id = Integer::intern(slot);
    Value* args[] = {vl.target, vl.id};
    vl.result = MxsCall(L"getAppData", args, 2);
    if (!vl.result || vl.result == &undefined) return std::nullopt;
    return WideToUtf8(vl.result->to_string());
}

void SetAppData(INode* node, int slot, const std::string& text, const std::string& what) {
    {
        ScopedMaxScriptEvaluationContext context;
        three_value_locals(target, id, value);
        vl.target = MAXNode::intern(node);
        vl.id = Integer::intern(slot);
        vl.value = new String(Utf8ToWide(text).c_str());
        Value* args[] = {vl.target, vl.id, vl.value};
        MxsCall(L"setAppData", args, 3);
    }
    if (GetAppData(node, slot) != text) throw MaxError("the " + what + " did not persist on '" + NodeName(node) + "'");
}

} // namespace MX_ABI_TAG
} // namespace mx
