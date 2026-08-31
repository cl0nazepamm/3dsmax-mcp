#pragma once

#include "mcp_bridge/handler_helpers.h"

#include <cctype>
#include <optional>
#include <string>
#include <vector>

namespace NodeRefs {

using HandlerHelpers::CollectNodes;
using HandlerHelpers::CollectNodesByExactName;
using HandlerHelpers::FindNodeByHandle;
using HandlerHelpers::NodeHandle;
using HandlerHelpers::NodeIdentityJson;
using HandlerHelpers::StructuredErrorPayload;
using HandlerHelpers::WideToUtf8;
using HandlerHelpers::json;

inline std::string EncodePathSegment(const std::string& value) {
    std::string encoded;
    encoded.reserve(value.size());
    for (char ch : value) {
        if (ch == '~') encoded += "~0";
        else if (ch == '/') encoded += "~1";
        else encoded += ch;
    }
    return encoded;
}

inline std::string DecodePathSegment(const std::string& value) {
    std::string decoded;
    decoded.reserve(value.size());
    for (size_t i = 0; i < value.size(); ++i) {
        if (value[i] != '~') {
            decoded += value[i];
            continue;
        }
        if (i + 1 >= value.size() || (value[i + 1] != '0' && value[i + 1] != '1')) {
            throw std::runtime_error(StructuredErrorPayload(
                "BAD_NODE_REF",
                "NodeRef path contains an invalid JSON Pointer escape."));
        }
        decoded += value[++i] == '0' ? '~' : '/';
    }
    return decoded;
}

inline std::vector<std::string> ParsePath(const std::string& path) {
    if (path.empty() || path.front() != '/') {
        throw std::runtime_error(StructuredErrorPayload(
            "BAD_NODE_REF",
            "NodeRef path must be an absolute JSON Pointer such as /Parent/Child."));
    }

    std::vector<std::string> segments;
    size_t begin = 1;
    while (begin <= path.size()) {
        const size_t end = path.find('/', begin);
        const std::string encoded = path.substr(
            begin,
            end == std::string::npos ? std::string::npos : end - begin);
        if (encoded.empty()) {
            throw std::runtime_error(StructuredErrorPayload(
                "BAD_NODE_REF",
                "NodeRef path cannot contain empty hierarchy segments."));
        }
        segments.push_back(DecodePathSegment(encoded));
        if (end == std::string::npos) break;
        begin = end + 1;
    }
    return segments;
}

inline std::string CanonicalizePath(const std::string& path) {
    const auto segments = ParsePath(path);
    std::string canonical;
    for (const auto& segment : segments) {
        canonical += "/" + EncodePathSegment(segment);
    }
    return canonical;
}

inline std::string BuildPath(INode* node) {
    if (!node || node->IsRootNode()) return {};

    std::vector<std::string> reversed;
    INode* current = node;
    while (current && !current->IsRootNode()) {
        reversed.push_back(WideToUtf8(current->GetName()));
        current = current->GetParentNode();
    }

    std::string path;
    for (auto it = reversed.rbegin(); it != reversed.rend(); ++it) {
        path += "/" + EncodePathSegment(*it);
    }
    return path;
}

inline json ToJson(INode* node, bool includeIdentity = false) {
    json result;
    if (!node) return result;
    result["handle"] = NodeHandle(node);
    result["name"] = WideToUtf8(node->GetName());
    result["path"] = BuildPath(node);
    if (includeIdentity) {
        const json identity = NodeIdentityJson(node);
        result["class"] = identity.value("class", "Unknown");
        result["layer"] = identity.value("layer", "0");
    }
    return result;
}

inline json CandidateList(const std::vector<INode*>& nodes) {
    json candidates = json::array();
    for (INode* node : nodes) candidates.push_back(ToJson(node, true));
    return candidates;
}

inline INode* ResolvePath(const std::string& path) {
    const auto segments = ParsePath(path);
    INode* current = GetCOREInterface()->GetRootNode();

    for (const auto& segment : segments) {
        std::vector<INode*> matches;
        for (int i = 0; i < current->NumberOfChildren(); ++i) {
            INode* child = current->GetChildNode(i);
            if (child && WideToUtf8(child->GetName()) == segment) matches.push_back(child);
        }
        if (matches.empty()) {
            throw std::runtime_error(StructuredErrorPayload(
                "NOT_FOUND",
                "NodeRef path not found: " + path));
        }
        if (matches.size() > 1) {
            throw std::runtime_error(StructuredErrorPayload(
                "AMBIGUOUS",
                "Ambiguous NodeRef path: " + path,
                {
                    {"message", "Sibling names are duplicated; pass a handle."},
                    {"candidates", CandidateList(matches)},
                }));
        }
        current = matches.front();
    }
    return current;
}

inline unsigned long long ParseHandle(const json& value) {
    try {
        if (value.is_number_unsigned()) return value.get<unsigned long long>();
        if (value.is_number_integer()) {
            const long long parsed = value.get<long long>();
            if (parsed > 0) return static_cast<unsigned long long>(parsed);
        }
        // MAXScript headers define an is_string macro, so compare the JSON type.
        if (value.type() == json::value_t::string) {
            const std::string text = value.get<std::string>();
            if (text.empty() || !std::all_of(text.begin(), text.end(), [](unsigned char ch) {
                    return std::isdigit(ch) != 0;
                })) {
                throw std::invalid_argument("not a decimal handle");
            }
            size_t consumed = 0;
            const unsigned long long parsed = std::stoull(text, &consumed);
            if (parsed > 0 && consumed == text.size()) return parsed;
        }
    } catch (...) {
    }
    throw std::runtime_error(StructuredErrorPayload(
        "BAD_NODE_REF",
        "NodeRef handle must be a positive integer or decimal string."));
}

inline INode* Resolve(const json& ref) {
    if (ref.is_number_integer() || ref.is_number_unsigned() ||
        ref.type() == json::value_t::string) {
        if (ref.type() == json::value_t::string) {
            const std::string text = ref.get<std::string>();
            if (text.empty()) {
                throw std::runtime_error(StructuredErrorPayload(
                    "BAD_NODE_REF", "NodeRef name or path cannot be empty."));
            }
            if (!text.empty() && text.front() == '/') return ResolvePath(text);
            const auto matches = CollectNodesByExactName(text);
            if (matches.empty()) {
                throw std::runtime_error(StructuredErrorPayload(
                    "NOT_FOUND", "NodeRef name not found: " + text));
            }
            if (matches.size() > 1) {
                throw std::runtime_error(StructuredErrorPayload(
                    "AMBIGUOUS",
                    "Ambiguous NodeRef name: " + text,
                    {{"message", "Pass a handle or hierarchy path."},
                     {"candidates", CandidateList(matches)}}));
            }
            return matches.front();
        }
        const unsigned long long handle = ParseHandle(ref);
        INode* node = FindNodeByHandle(handle);
        if (!node) {
            throw std::runtime_error(StructuredErrorPayload(
                "NOT_FOUND", "NodeRef handle not found: " + std::to_string(handle)));
        }
        return node;
    }

    if (!ref.is_object()) {
        throw std::runtime_error(StructuredErrorPayload(
            "BAD_NODE_REF",
            "NodeRef must be an object with handle, name, or path."));
    }

    if (ref.contains("name") && !ref["name"].is_null() &&
        ref["name"].type() != json::value_t::string) {
        throw std::runtime_error(StructuredErrorPayload(
            "BAD_NODE_REF", "NodeRef name must be a non-empty string."));
    }
    if (ref.contains("path") && !ref["path"].is_null() &&
        ref["path"].type() != json::value_t::string) {
        throw std::runtime_error(StructuredErrorPayload(
            "BAD_NODE_REF", "NodeRef path must be a non-empty string."));
    }
    if (ref.contains("name") && ref["name"].type() == json::value_t::string &&
        ref["name"].get<std::string>().empty()) {
        throw std::runtime_error(StructuredErrorPayload(
            "BAD_NODE_REF", "NodeRef name must be a non-empty string."));
    }
    if (ref.contains("path") && ref["path"].type() == json::value_t::string &&
        ref["path"].get<std::string>().empty()) {
        throw std::runtime_error(StructuredErrorPayload(
            "BAD_NODE_REF", "NodeRef path must be a non-empty string."));
    }

    const bool hasHandle = ref.contains("handle") && !ref["handle"].is_null();
    const bool hasName = ref.contains("name") && ref["name"].type() == json::value_t::string &&
        !ref["name"].get<std::string>().empty();
    const bool hasPath = ref.contains("path") && ref["path"].type() == json::value_t::string &&
        !ref["path"].get<std::string>().empty();

    if (!hasHandle && !hasName && !hasPath) {
        throw std::runtime_error(StructuredErrorPayload(
            "BAD_NODE_REF",
            "NodeRef requires at least one of handle, name, or path."));
    }

    INode* node = nullptr;
    if (hasHandle) {
        const unsigned long long handle = ParseHandle(ref["handle"]);
        node = FindNodeByHandle(handle);
        if (!node) {
            throw std::runtime_error(StructuredErrorPayload(
                "NOT_FOUND", "NodeRef handle not found: " + std::to_string(handle)));
        }
        if (hasName && WideToUtf8(node->GetName()) != ref["name"].get<std::string>()) {
            throw std::runtime_error(StructuredErrorPayload(
                "NODE_REF_MISMATCH",
                "NodeRef handle and name identify different nodes.",
                {{"resolved", ToJson(node, true)}}));
        }
        if (hasPath && BuildPath(node) != CanonicalizePath(ref["path"].get<std::string>())) {
            throw std::runtime_error(StructuredErrorPayload(
                "NODE_REF_MISMATCH",
                "NodeRef handle and path identify different nodes.",
                {{"resolved", ToJson(node, true)}}));
        }
        return node;
    }

    if (hasPath) {
        node = ResolvePath(ref["path"].get<std::string>());
        if (hasName && WideToUtf8(node->GetName()) != ref["name"].get<std::string>()) {
            throw std::runtime_error(StructuredErrorPayload(
                "NODE_REF_MISMATCH",
                "NodeRef path and name identify different nodes.",
                {{"resolved", ToJson(node, true)}}));
        }
        return node;
    }

    const std::string name = ref["name"].get<std::string>();
    const auto matches = CollectNodesByExactName(name);
    if (matches.empty()) {
        throw std::runtime_error(StructuredErrorPayload(
            "NOT_FOUND", "NodeRef name not found: " + name));
    }
    if (matches.size() > 1) {
        throw std::runtime_error(StructuredErrorPayload(
            "AMBIGUOUS",
            "Ambiguous NodeRef name: " + name,
            {{"message", "Pass a handle or hierarchy path."},
             {"candidates", CandidateList(matches)}}));
    }
    return matches.front();
}

} // namespace NodeRefs
