// json_mini.h — tiny flat-object JSON parser for the overlay IPC protocol.
//
// We only ever receive single-level objects with string values from
// client.py, e.g.:
//   {"type":"item_received","item":"Baton","from":"Alice"}
// so a full JSON library is overkill. This parses exactly that shape and
// nothing else (no nesting, no arrays, no numbers/bools — everything comes
// across the wire as a JSON string on the Python side).
#pragma once
#include <string>
#include <unordered_map>
#include <cctype>

namespace json_mini {

inline bool Parse(const std::string& text, std::unordered_map<std::string, std::string>& out) {
    out.clear();
    size_t i = 0;
    size_t n = text.size();

    auto skip_ws = [&]() {
        while (i < n && std::isspace(static_cast<unsigned char>(text[i]))) ++i;
    };

    auto parse_string = [&](std::string& result) -> bool {
        if (i >= n || text[i] != '"') return false;
        ++i;
        result.clear();
        while (i < n && text[i] != '"') {
            char c = text[i];
            if (c == '\\' && i + 1 < n) {
                char e = text[i + 1];
                switch (e) {
                    case 'n': result += '\n'; break;
                    case 't': result += '\t'; break;
                    case 'r': result += '\r'; break;
                    case '"': result += '"'; break;
                    case '\\': result += '\\'; break;
                    case '/': result += '/'; break;
                    default: result += e; break;
                }
                i += 2;
            } else {
                result += c;
                ++i;
            }
        }
        if (i >= n) return false; // unterminated string
        ++i; // closing quote
        return true;
    };

    skip_ws();
    if (i >= n || text[i] != '{') return false;
    ++i;
    skip_ws();

    if (i < n && text[i] == '}') { ++i; return true; } // empty object

    while (i < n) {
        skip_ws();
        std::string key;
        if (!parse_string(key)) return false;
        skip_ws();
        if (i >= n || text[i] != ':') return false;
        ++i;
        skip_ws();

        std::string value;
        if (i < n && text[i] == '"') {
            if (!parse_string(value)) return false;
        } else {
            // Bare literal (number/true/false/null) — capture as raw text,
            // we don't currently need any of these but don't want to choke
            // on them either.
            size_t start = i;
            while (i < n && text[i] != ',' && text[i] != '}') ++i;
            value = text.substr(start, i - start);
        }
        out[key] = value;

        skip_ws();
        if (i < n && text[i] == ',') { ++i; continue; }
        if (i < n && text[i] == '}') { ++i; break; }
        return false;
    }
    return true;
}

// Escape() — the write-side counterpart to Parse() above, added 2026-08-04
// for the in-game connect/console panel (overlay.cpp): building outbound
// JSON (server address, player name, free-typed console command text) by
// hand means any of those fields could legitimately contain a `"` or `\`
// (or, less likely, a raw control character) that would otherwise produce
// invalid JSON on the wire. Only handles what a flat single-level object
// with string values needs — same deliberately-narrow scope as Parse().
inline std::string Escape(const std::string& in) {
    std::string out;
    out.reserve(in.size() + 8);
    for (char c : in) {
        switch (c) {
            case '"':  out += "\\\""; break;
            case '\\': out += "\\\\"; break;
            case '\n': out += "\\n";  break;
            case '\r': out += "\\r";  break;
            case '\t': out += "\\t";  break;
            default:
                if (static_cast<unsigned char>(c) < 0x20) {
                    // Other control characters: drop rather than emit
                    // something that would break the parser on the
                    // receiving (client.py, real json.loads) end.
                } else {
                    out += c;
                }
        }
    }
    return out;
}

} // namespace json_mini
