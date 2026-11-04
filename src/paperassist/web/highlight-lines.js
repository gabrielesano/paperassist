// Merge nearby selected word boxes on each baseline, keeping column gutters clear.
export function mergeHighlightLines(boxes, rotated = false, gapScale = 1) {
    const along = rotated ? "y" : "x";
    const across = rotated ? "x" : "y";
    const length = rotated ? "height" : "width";
    const thickness = rotated ? "width" : "height";
    const lines = [];
    const ordered = [...boxes].sort((a, b) => a[along] - b[along]);
    for (const box of ordered) {
        const middle = box[across] + box[thickness] / 2;
        const line = lines.find((candidate) => {
            const center = candidate[across] + candidate[thickness] / 2;
            const gap = box[along] - (candidate[along] + candidate[length]);
            return (
                Math.abs(center - middle) <
                    Math.min(candidate[thickness], box[thickness]) * 0.4 &&
                gap <=
                    Math.max(candidate[thickness], box[thickness]) *
                        gapScale *
                        2
            );
        });
        if (!line) {
            lines.push({ ...box });
            continue;
        }
        const end = Math.max(
            line[along] + line[length],
            box[along] + box[length],
        );
        const edge = Math.max(
            line[across] + line[thickness],
            box[across] + box[thickness],
        );
        line[along] = Math.min(line[along], box[along]);
        line[across] = Math.min(line[across], box[across]);
        line[length] = end - line[along];
        line[thickness] = edge - line[across];
    }
    return lines;
}
