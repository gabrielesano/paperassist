"""Engine-private PDF annotation reading and incremental writing.

Explicit appearances make the same revision render identically before and after
Save. Recoloring preserves existing curves/resources instead of guessing their shape.
"""
import io
from datetime import datetime, timezone

from pypdf import PdfReader, PdfWriter
from pypdf.generic import (ArrayObject, ContentStream, DecodedStreamObject, DictionaryObject,
                           FloatObject, NameObject, NullObject, NumberObject, RectangleObject, TextStringObject)


def incremental_writer(data):
    writer = PdfWriter(io.BytesIO(data), incremental=True)
    # A previous xref stream may not index itself. Reusing its number makes pypdf
    # cache that old xref as a new annotation, so reserve the full declared /Size.
    size = int(writer._reader.trailer['/Size']) - 1
    writer._objects.extend([None] * max(0, size - len(writer._objects)))
    return writer


def numbers(values):
    return ArrayObject(FloatObject(value) for value in values)


def bounds(points, pad=0):
    xs, ys = zip(*points)
    return min(xs) - pad, min(ys) - pad, max(xs) + pad, max(ys) + pad


def value(annotation, key, default):
    item = annotation.get(key)
    item = item.get_object() if item is not None else None
    return default if item is None or isinstance(item, NullObject) else item


def ink_strokes(annotation):
    return [stroke.get_object() for stroke in value(annotation, '/InkList', [])]


def color_rgb(annotation):
    color = list(value(annotation, '/C', [1, 1, 0] if annotation['/Subtype'] == '/Highlight' else [0, 0, 0]))
    if len(color) == 1:
        return color * 3
    if len(color) == 4:
        c, m, y, k = color
        return [1 - min(1, channel + k) for channel in (c, m, y)]
    return color if len(color) == 3 else [0, 0, 0]


def read(data, page):
    reader = PdfReader(io.BytesIO(data))
    result = []
    for index, reference in enumerate(reader.pages[page].get('/Annots', ArrayObject()).get_object()):
        annotation = reference.get_object()
        subtype = annotation.get('/Subtype')
        if subtype not in ('/Highlight', '/Ink'):
            continue
        points = value(annotation, '/QuadPoints', [])
        quads = [bounds(list(zip(points[i:i + 8:2], points[i + 1:i + 8:2]))) for i in range(0, len(points) - 7, 8)]
        strokes = [list(zip(stroke[::2], stroke[1::2])) for stroke in ink_strokes(annotation)]
        # /C remains authoritative even when PDFium's GetColor refuses an AP-bearing annotation.
        color = color_rgb(annotation)
        result.append(dict(index=index, subtype='highlight' if subtype == '/Highlight' else 'ink',
                           rect=tuple(annotation['/Rect']), quads=quads, strokes=strokes,
                           color=tuple(color), opacity=float(annotation.get('/CA', 1)),
                           width=float(value(annotation, '/BS', {}).get('/W', 1)),
                           freehand=annotation.get('/IT') == '/InkHighlight'))
    return result


def appearance(writer, box, content, opacity, multiply=False):
    graphics = DictionaryObject({NameObject('/Type'): NameObject('/ExtGState'),
                                 NameObject('/ca'): FloatObject(opacity), NameObject('/CA'): FloatObject(opacity)})
    if multiply:
        graphics[NameObject('/BM')] = NameObject('/Multiply')
    stream = DecodedStreamObject()
    stream.set_data(('/GS gs\n' + content).encode('ascii'))
    stream.update({NameObject('/Type'): NameObject('/XObject'), NameObject('/Subtype'): NameObject('/Form'),
                   NameObject('/FormType'): NumberObject(1), NameObject('/BBox'): RectangleObject(box),
                   NameObject('/Resources'): DictionaryObject({NameObject('/ExtGState'): DictionaryObject({NameObject('/GS'): graphics})})})
    return DictionaryObject({NameObject('/N'): writer._add_object(stream)})


def regenerate(writer, annotation):
    color = color_rgb(annotation)
    opacity = float(annotation.get('/CA', 1))
    if annotation['/Subtype'] == '/Highlight':
        points = annotation['/QuadPoints']
        content = '{:.6f} {:.6f} {:.6f} rg\n'.format(*color)
        for i in range(0, len(points), 8):
            corners = [points[i:i + 2], points[i + 2:i + 4], points[i + 6:i + 8], points[i + 4:i + 6]]
            content += ' '.join(f'{x} {y} {"m" if j == 0 else "l"}' for j, (x, y) in enumerate(corners)) + ' h f\n'
        multiply = True
    else:
        width = float(value(annotation, '/BS', {}).get('/W', 1))
        content = '{:.6f} {:.6f} {:.6f} RG {} w 1 J 1 j\n'.format(*color, width)
        for stroke in ink_strokes(annotation):
            content += ' '.join(f'{stroke[i]} {stroke[i + 1]} {"m" if i == 0 else "l"}' for i in range(0, len(stroke), 2)) + ' S\n'
        multiply = annotation.get('/IT') == '/InkHighlight'
    annotation[NameObject('/AP')] = appearance(writer, annotation['/Rect'], content, opacity, multiply)


def recolor_appearance(writer, original, color, seen=None):
    seen = set() if seen is None else seen
    seen.add(id(original))
    content = ContentStream(original, writer)
    for i, (operands, operator) in enumerate(content.operations):
        if operator in (b'rg', b'RG', b'g', b'G', b'k', b'K'):
            content.operations[i] = (numbers(color), b'rg' if operator.islower() else b'RG')
    replacement = DecodedStreamObject()
    replacement.update({key: value for key, value in original.items() if key not in ('/Length', '/Filter', '/DecodeParms')})
    replacement.set_data(content.get_data())
    resources = DictionaryObject(original.get('/Resources', DictionaryObject()).get_object())
    if '/XObject' in resources:
        objects = DictionaryObject(resources['/XObject'].get_object())
        for name, reference in list(objects.items()):
            child = reference.get_object()
            if child.get('/Subtype') == '/Form' and id(child) not in seen:
                objects[name] = writer._add_object(recolor_appearance(writer, child, color, seen))
        resources[NameObject('/XObject')] = objects
    replacement[NameObject('/Resources')] = resources
    return replacement


def apply(data, kind, page, **values):
    writer = incremental_writer(data)
    target = writer.pages[page]
    if kind in ('highlight', 'ink'):
        color, opacity = values['color'], values.get('opacity', 1)
        annotation = DictionaryObject({NameObject('/Type'): NameObject('/Annot'), NameObject('/F'): NumberObject(4),
                                       NameObject('/C'): numbers(color), NameObject('/CA'): FloatObject(opacity),
                                       NameObject('/CreationDate'): TextStringObject(datetime.now(timezone.utc).strftime('D:%Y%m%d%H%M%SZ'))})
        if kind == 'highlight':
            quads = values['rects']
            box = bounds([(r[i], r[i + 1]) for r in quads for i in (0, 2)])
            annotation.update({NameObject('/Subtype'): NameObject('/Highlight'), NameObject('/Rect'): RectangleObject(box),
                               NameObject('/QuadPoints'): numbers(v for x0, y0, x1, y1 in quads for v in (x0, y1, x1, y1, x0, y0, x1, y0))})
        else:
            strokes, width = values['strokes'], values['width']
            annotation.update({NameObject('/Subtype'): NameObject('/Ink'),
                               NameObject('/Rect'): RectangleObject(bounds([p for stroke in strokes for p in stroke], width / 2)),
                               NameObject('/InkList'): ArrayObject(numbers(v for point in stroke for v in point) for stroke in strokes),
                               NameObject('/BS'): DictionaryObject({NameObject('/W'): FloatObject(width)})})
            if values.get('freehand'):  # PDF.js's name for a thick multiplied stroke used as a highlighter
                annotation[NameObject('/IT')] = NameObject('/InkHighlight')
        regenerate(writer, annotation)
        writer.add_annotation(page, annotation)
    else:
        references = target['/Annots'].get_object()
        annotation = references[values['index']].get_object()
        if kind == 'delete':
            popup = annotation.get('/Popup')
            del references[values['index']]
            if popup in references:
                references.remove(popup)
        elif kind == 'transform':
            old = bounds([(float(annotation['/Rect'][i]), float(annotation['/Rect'][i + 1])) for i in (0, 2)])
            new = bounds([values['rect'][:2], values['rect'][2:]])
            sx = (new[2] - new[0]) / (old[2] - old[0]) if old[2] != old[0] else 1
            sy = (new[3] - new[1]) / (old[3] - old[1]) if old[3] != old[1] else 1
            annotation[NameObject('/InkList')] = ArrayObject(
                numbers(new[0] + (v - old[0]) * sx if i % 2 == 0 else new[1] + (v - old[1]) * sy
                        for i, v in enumerate(stroke)) for stroke in ink_strokes(annotation))
            annotation[NameObject('/Rect')] = RectangleObject(new)
            if value(annotation, '/BS', None) is not None:
                border = DictionaryObject(value(annotation, '/BS', {}))
                border[NameObject('/W')] = FloatObject(float(border.get('/W', 1)) * sx)
                annotation[NameObject('/BS')] = border
        elif kind == 'update':
            color, width = values.get('color'), values.get('width')
            if color is not None:
                annotation[NameObject('/C')] = numbers(color)
            if width is not None:
                annotation[NameObject('/BS')] = DictionaryObject({NameObject('/W'): FloatObject(width)})
                points = [p for stroke in ink_strokes(annotation) for p in zip(stroke[::2], stroke[1::2])]
                annotation[NameObject('/Rect')] = RectangleObject(bounds(points, width / 2))
            normal = value(value(annotation, '/AP', {}), '/N', None)
            if normal and width is None:
                appearances = DictionaryObject(annotation['/AP'].get_object())
                appearances[NameObject('/N')] = writer._add_object(recolor_appearance(writer, normal.get_object(), color))
                annotation[NameObject('/AP')] = appearances
            else:
                regenerate(writer, annotation)
    output = io.BytesIO()
    writer.write(output)
    return output.getvalue()


def restore(data, previous, pages):
    """Restore annotation arrays in another incremental update; even Undo keeps the saved prefix."""
    writer = incremental_writer(data)
    reader = PdfReader(io.BytesIO(previous))
    # Link destinations and annotation /P keys must point to the existing pages, not clones.
    translation = writer._id_translated.setdefault(id(reader), {})
    for source, target in zip(reader.pages, writer.pages):
        translation[source.indirect_reference.idnum] = target.indirect_reference.idnum
    for page in pages:
        array = reader.pages[page].get('/Annots', ArrayObject()).get_object()
        writer.pages[page][NameObject('/Annots')] = ArrayObject(
            item.clone(writer, force_duplicate=True) for item in array)
    output = io.BytesIO()
    writer.write(output)
    return output.getvalue()


def without_highlights(data):
    """A render-only revision; the editable document and source file stay untouched."""
    writer = incremental_writer(data)
    for page in writer.pages:
        if '/Annots' in page:
            page[NameObject('/Annots')] = ArrayObject(
                ref for ref in page['/Annots'] if ref.get_object().get('/Subtype') != '/Highlight'
                and ref.get_object().get('/IT') != '/InkHighlight')
    output = io.BytesIO()
    writer.write(output)
    return output.getvalue()
