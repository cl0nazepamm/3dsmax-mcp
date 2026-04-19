#pragma once
#include <windows.h>
#include <string>
#include <vector>
#include <utility>
#include <functional>

#include <max.h>
#include <maxapi.h>

class MCPBridgeGUP;

namespace MCPChatUI {
    void Init(HINSTANCE hInst);
    void Show(MCPBridgeGUP* gup);
    void Hide();
    void Destroy();
    bool IsVisible();

    // Append text to chat history
    void AppendMessage(const std::string& role, const std::string& text);

    // Set callback for when user sends a message
    using MessageCallback = std::function<void(const std::string& text)>;
    using ActionCallback = std::function<void(const std::string& action, const std::string& detail)>;
    void SetMessageCallback(MessageCallback cb);
    void SetActionCallback(ActionCallback cb);

    // Show status in title bar
    void SetStatus(const std::string& status);
}
