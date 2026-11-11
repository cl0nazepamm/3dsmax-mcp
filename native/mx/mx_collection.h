// The collection protocol. What the scene, or 3ds Max, has many of is one
// object of the module, and every one of them answers the same way:
//
//   nodes['Ball']        the item of that key; NotFound or Ambiguous otherwise
//   nodes.get('Ball')    the same item, or None when there is none
//   'Ball' in nodes      whether [key] finds one
//   len(nodes), for node in nodes, list(nodes)
//   nodes('Wall*')       the items a wildcard and the collection's own filters
//                        match, as a list; nodes() is all of them
//   nodes.new(...)       make one, on a collection that can
//   nodes.remove(x)      delete, on a collection that can
//
// BindCollection binds the shared part from two functions. The collection's
// own file adds __call__ with its filters, and new and remove where they
// exist. A module function never does what a collection does: lookup is [key],
// listing is the call, creation is new (maxmcp/mx/_reference.py lints it).
#pragma once

#include "mx_common.h"

namespace mx {
inline namespace MX_ABI_TAG {

// Tag: an empty struct that is the collection's Python class. Item: what it
// holds. Key: what [key] takes, as the argument type the signature shows.
// all() lists every item; find(key) gives one or throws NotFound or Ambiguous.
template <typename Tag, typename Item, typename Key, typename All, typename Find>
nb::class_<Tag> BindCollection(nb::module_& m, const char* name, const char* className, const char* doc, All all,
                               Find find) {
    nb::class_<Tag> cls(m, className, doc);
    cls.def("__getitem__", [find](const Tag&, Key key) -> Item { return find(key); }, "key"_a)
        .def("get", [find](const Tag&, Key key) -> std::optional<Item> {
            try {
                return find(key);
            } catch (const NotFound&) {
                return std::nullopt;
            }
        }, "key"_a, "The item [key] gives, or None when there is none; a key several items share is still Ambiguous.")
        .def("__contains__", [find](const Tag&, nb::handle key) {
            Key wanted{};
            if (!nb::try_cast<Key>(key, wanted)) return false;
            try {
                find(wanted);
                return true;
            } catch (const Ambiguous&) {
                return true;
            } catch (const MaxError&) {
                return false;
            }
        }, nb::arg("key").none())
        .def("__iter__", [all](const Tag&) { return nb::iter(nb::cast(all())); })
        .def("__len__", [all](const Tag&) { return all().size(); })
        .def("__repr__", [all, text = std::string(name)](const Tag&) {
            return "<" + text + ": " + std::to_string(all().size()) + ">";
        });
    m.attr(name) = nb::cast(Tag{});
    return cls;
}

// The one item of `items` named `key`, as [key] gives it: NotFound with a
// suggestion when there is none, Ambiguous when several share the name. `what`
// names the kind in the message ("material"); name(item) reads an item's name.
template <typename Item, typename NameOf>
Item FindByName(const std::vector<Item>& items, const std::string& key, const char* what, NameOf name) {
    std::vector<std::string> names;
    const Item* found = nullptr;
    int matches = 0;
    for (const Item& item : items) {
        std::string text = name(item);
        if (text == key) {
            if (!found) found = &item;
            ++matches;
        }
        names.push_back(std::move(text));
    }
    if (matches > 1) throw Ambiguous(std::to_string(matches) + " " + what + "s are named '" + key + "'");
    if (!found) throw NotFound(std::string("no ") + what + " named '" + key + "'." + Suggest(key, names));
    return *found;
}

} // namespace MX_ABI_TAG
} // namespace mx
