#include "mcp_bridge/qt_dialogs.h"
#include <QtWidgets/QAbstractButton>
#include <QtWidgets/QCheckBox>
#include <QtWidgets/QComboBox>
#include <QtWidgets/QLabel>
#include <QtWidgets/QLineEdit>
#include <QtWidgets/QMessageBox>
#include <QtWidgets/QPlainTextEdit>
#include <QtWidgets/QPushButton>
#include <QtWidgets/QRadioButton>
#include <QtWidgets/QTextEdit>
#include <QtWidgets/QToolButton>
#include <QtWidgets/QWidget>
#include <algorithm>
#include <cstring>
#include <stdexcept>
#include <string>
#include <vector>

// Built against the oldest Qt of each major version Max ships (5.15.1, 6.5.3)
// and run against Max's own, newer-or-equal Qt DLLs. Keep to long-stable API:
// every new Qt call must also be added to native/third_party/qt/*.def.
namespace QtDialogs {
using json = nlohmann::json;
namespace {
constexpr size_t kMaxText = 16384, kMaxField = 4096;
constexpr int kMaxButtons = 64, kMaxFields = 32, kMaxOther = 16;

std::string Utf8(const QString& text) {
    const QByteArray bytes = text.toUtf8();
    return std::string(bytes.constData(), static_cast<size_t>(bytes.size()));
}

std::string WithoutMnemonic(const std::string& text) {
    std::string out;
    for (size_t i = 0; i < text.size(); ++i) {
        if (text[i] == '&' && i + 1 < text.size()) ++i;
        out += text[i];
    }
    return out;
}

// Labels and message boxes often hold rich text. Reduce it to readable plain
// text without depending on QtGui's document classes.
std::string PlainText(const std::string& text) {
    if (text.find('<') == std::string::npos) return text;
    std::string out;
    for (size_t i = 0; i < text.size();) {
        if (text[i] == '<') {
            const size_t end = text.find('>', i);
            if (end == std::string::npos) break;
            std::string tag = text.substr(i + 1, end - i - 1);
            std::transform(tag.begin(), tag.end(), tag.begin(), [](unsigned char c) { return static_cast<char>(tolower(c)); });
            if (tag.rfind("br", 0) == 0 || tag == "p" || tag == "/p" || tag.rfind("p ", 0) == 0 ||
                tag == "li" || tag == "/div" || tag == "/tr" || tag == "/h1" || tag == "/h2" || tag == "/h3")
                if (!out.empty() && out.back() != '\n') out += '\n';
            i = end + 1;
        } else if (text[i] == '&') {
            static const std::pair<const char*, const char*> entities[] = {
                {"&amp;", "&"}, {"&lt;", "<"}, {"&gt;", ">"}, {"&quot;", "\""}, {"&#39;", "'"}, {"&nbsp;", " "}};
            bool matched = false;
            for (const auto& [name, value] : entities) {
                if (text.compare(i, strlen(name), name) == 0) {
                    out += value;
                    i += strlen(name);
                    matched = true;
                    break;
                }
            }
            if (!matched) out += text[i++];
        } else {
            out += text[i++];
        }
    }
    while (!out.empty() && (out.back() == '\n' || out.back() == ' ')) out.pop_back();
    return out;
}

std::string Clip(std::string text, size_t limit, bool& truncated) {
    if (text.size() > limit) {
        text.resize(limit);
        truncated = true;
    }
    return text;
}

QWidget* Window(HWND hwnd) {
    QWidget* widget = QWidget::find(reinterpret_cast<WId>(hwnd));
    if (!widget) return nullptr;
    widget = widget->window();
    return widget && widget->isVisible() ? widget : nullptr;
}

// Composite widgets report their own value; their internal children (a
// combo box's line edit, a text edit's viewport) are not separate controls.
bool InsideComposite(QWidget* widget, QWidget* window) {
    for (QWidget* parent = widget->parentWidget(); parent && parent != window; parent = parent->parentWidget()) {
        if (parent->inherits("QComboBox") || parent->inherits("QAbstractSpinBox") ||
            parent->inherits("QAbstractItemView") || parent->inherits("QTextEdit") ||
            parent->inherits("QPlainTextEdit") || parent->inherits("QAbstractButton"))
            return true;
    }
    return false;
}

// Visible descendants in reading order (top to bottom, then left to right).
std::vector<QWidget*> Controls(QWidget* window) {
    std::vector<std::pair<QPoint, QWidget*>> placed;
    const QList<QWidget*> children = window->findChildren<QWidget*>();
    for (QWidget* child : children) {
        if (!child->isVisibleTo(window) || InsideComposite(child, window)) continue;
        placed.push_back({child->mapTo(window, QPoint(0, 0)), child});
    }
    std::stable_sort(placed.begin(), placed.end(), [](const auto& a, const auto& b) {
        return a.first.y() != b.first.y() ? a.first.y() < b.first.y() : a.first.x() < b.first.x();
    });
    std::vector<QWidget*> result;
    for (const auto& item : placed) result.push_back(item.second);
    return result;
}

std::vector<QAbstractButton*> Buttons(QWidget* window) {
    std::vector<QAbstractButton*> result;
    for (QWidget* widget : Controls(window))
        if (auto* button = qobject_cast<QAbstractButton*>(widget))
            if (result.size() < static_cast<size_t>(kMaxButtons)) result.push_back(button);
    return result;
}

const char* RoleName(QMessageBox::ButtonRole role) {
    switch (role) {
    case QMessageBox::AcceptRole: return "accept";
    case QMessageBox::RejectRole: return "reject";
    case QMessageBox::DestructiveRole: return "destructive";
    case QMessageBox::ActionRole: return "action";
    case QMessageBox::HelpRole: return "help";
    case QMessageBox::YesRole: return "yes";
    case QMessageBox::NoRole: return "no";
    case QMessageBox::ResetRole: return "reset";
    case QMessageBox::ApplyRole: return "apply";
    default: return nullptr;
    }
}
}

json Snapshot(HWND hwnd) {
    QWidget* window = Window(hwnd);
    if (!window) return nullptr;
    auto* box = qobject_cast<QMessageBox*>(window);
    bool truncated = false;
    std::string text;
    json buttons = json::array(), fields = json::array(), other = json::array();
    const auto all_buttons = Buttons(window);
    for (QWidget* widget : Controls(window)) {
        if (auto* button = qobject_cast<QAbstractButton*>(widget)) {
            const auto found = std::find(all_buttons.begin(), all_buttons.end(), button);
            if (found == all_buttons.end()) { truncated = true; continue; }
            std::string label = WithoutMnemonic(Utf8(button->text()));
            if (label.empty()) label = Utf8(button->toolTip());
            if (label.empty()) label = Utf8(button->accessibleName());
            json item = {{"index", static_cast<int>(found - all_buttons.begin())}, {"label", label},
                {"enabled", button->isEnabled()}};
            item["kind"] = qobject_cast<QCheckBox*>(button) ? "check" : qobject_cast<QRadioButton*>(button) ? "radio"
                : qobject_cast<QToolButton*>(button) ? "tool" : "push";
            if (button->isCheckable()) item["checked"] = button->isChecked();
            if (auto* push = qobject_cast<QPushButton*>(button)) item["default"] = push->isDefault();
            if (box) {
                if (const char* role = RoleName(box->buttonRole(button))) item["role"] = role;
                if (box->defaultButton() == button) item["default"] = true;
                if (box->escapeButton() == button) item["escape"] = true;
            }
            buttons.push_back(std::move(item));
        } else if (auto* label = qobject_cast<QLabel*>(widget)) {
            const std::string value = PlainText(Utf8(label->text()));
            if (!value.empty()) text += value + "\n";
        } else if (fields.size() < static_cast<size_t>(kMaxFields)) {
            json field;
            if (auto* edit = qobject_cast<QLineEdit*>(widget)) {
                field = {{"kind", "edit"}, {"read_only", edit->isReadOnly()}};
                if (edit->echoMode() == QLineEdit::Normal) field["value"] = Clip(Utf8(edit->text()), kMaxField, truncated);
            } else if (auto* rich = qobject_cast<QTextEdit*>(widget)) {
                field = {{"kind", "text"}, {"read_only", rich->isReadOnly()},
                    {"value", Clip(Utf8(rich->toPlainText()), kMaxField, truncated)}};
            } else if (auto* plain = qobject_cast<QPlainTextEdit*>(widget)) {
                field = {{"kind", "text"}, {"read_only", plain->isReadOnly()},
                    {"value", Clip(Utf8(plain->toPlainText()), kMaxField, truncated)}};
            } else if (auto* combo = qobject_cast<QComboBox*>(widget)) {
                field = {{"kind", "combo"}, {"value", Utf8(combo->currentText())}};
            } else if (widget->focusPolicy() != Qt::NoFocus && other.size() < static_cast<size_t>(kMaxOther)) {
                // Interactive controls this reader does not understand.
                other.push_back(widget->metaObject()->className());
            }
            if (!field.is_null()) fields.push_back(std::move(field));
        }
    }
    if (box) {
        const std::string details = Utf8(box->detailedText());
        if (!details.empty()) fields.push_back({{"kind", "details"}, {"value", Clip(details, kMaxField, truncated)}});
    }
    if (!text.empty()) text.pop_back();
    json result = {{"kind", "qt"}, {"qt_class", window->metaObject()->className()},
        {"title", Utf8(window->windowTitle())}, {"text", Clip(text, kMaxText, truncated)},
        {"buttons", buttons}, {"fields", fields}, {"modal", window->isModal()},
        {"complete", !truncated && other.empty()}};
    if (!other.empty()) result["other_controls"] = other;
    return result;
}

void Click(HWND hwnd, int index) {
    QWidget* window = Window(hwnd);
    if (!window) throw std::runtime_error("STALE_DIALOG: dialog closed");
    const auto buttons = Buttons(window);
    if (index < 0 || index >= static_cast<int>(buttons.size())) throw std::runtime_error("STALE_DIALOG: button is gone");
    QAbstractButton* button = buttons[static_cast<size_t>(index)];
    if (!button->isEnabled()) throw std::runtime_error("BUTTON_DISABLED: the button is disabled");
    button->click();
}
}
