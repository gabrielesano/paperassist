// Case- and accent-insensitive, so "Antolín" is found by typing "antolin".
const fold = (text) => text.normalize("NFKD").replace(/\p{M}/gu, "").toLowerCase();

// Every word must match somewhere in the entry, in any order: "antolin 2012" or "2012 antolin".
export function referenceMatcher(query) {
    const words = fold(query).split(/\s+/).filter(Boolean);
    return (ref) => {
        const text = fold([ref.label, ref.authors, ref.year, ref.doi, ref.title, ref.raw].filter(Boolean).join(" "));
        return words.every((word) => text.includes(word));
    };
}
