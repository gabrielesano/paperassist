#!/usr/bin/env python3
"""Collect and format third-party license notices for PaperAssist standalone distribution.

Invoked during standalone packaging (or standalone CLI) to produce THIRD-PARTY-NOTICES.txt.
"""
import io
import json
import os
import platform
import re
import subprocess
import sys
from importlib import metadata
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# Full text of LGPL-3.0, GPL-3.0, Apache-2.0, BSD, MIT, etc.
LGPL_3_TEXT = """\
GNU LESSER GENERAL PUBLIC LICENSE
Version 3, 29 June 2007

Copyright (C) 2007 Free Software Foundation, Inc. <https://fsf.org/>
Everyone is permitted to copy and distribute verbatim copies
of this license document, but changing it is not allowed.

This version of the GNU Lesser General Public License incorporates
the terms and conditions of version 3 of the GNU General Public
License, supplemented by the additional permissions listed below.

0. Additional Definitions.

As used herein, "this License" refers to version 3 of the GNU Lesser
General Public License, and the "GNU GPL" refers to version 3 of the GNU
General Public License.

"The Library" refers to a covered work governed by this License,
other than an Application or a Combined Work as defined below.

An "Application" is any work that makes use of an interface provided
by the Library, but which is not otherwise based on the Library.
Defining a subclass of a class defined by the Library is deemed a mode
of using an interface provided by the Library.

A "Combined Work" is a work produced by combining or linking an
Application with the Library.  The particular version of the Library
with which the Combined Work was made is also called the "Linked Version".

The "Minimal Corresponding Source" for a Combined Work means the
Corresponding Source for the Combined Work, excluding any source code
for portions of the Combined Work that, considered in isolation, are
based on the Application, and not on the Linked Version.

The "Corresponding Application Code" for a Combined Work means the
object code and/or source code for the Application, including any data
and utility programs needed for reproducing the Combined Work from the
Application, but excluding the System Libraries of the Combined Work.

1. Exception to Section 3 of the GNU GPL.

You may convey a covered work under sections 3 and 4 of this License
without being bound by section 3 of the GNU GPL.

2. Conveying Modified Versions.

If you modify a copy of the Library, and, in your modifications, a
facility refers to a function or data to be supplied by an Application
that uses the facility (other than as an argument passed when the
facility is invoked), then you may convey a copy of the modified
version:

  a) under this License, provided that you make a good faith effort to
  ensure that, in the event an Application does not supply the
  function or data, the facility still operates, and performs
  whatever part of its purpose remains meaningful, or

  b) under the GNU GPL, with none of the additional permissions of
  this License applicable to that copy.

3. Object Code Incorporating Material from Library Header Files.

The object code form of an Application may incorporate material from
a header file that is part of the Library.  You may convey such object
code under terms of your choice, provided that, if the incorporated
material is not limited to numerical parameters, data structure
layouts and accessors, or small macros, inline functions and templates
(ten or fewer lines in length), you do both of the following:

  a) Give prominent notice with each copy of the object code that the
  Library is used in it and that the Library and its use are
  covered by this License.

  b) Accompany the object code with a copy of the GNU GPL and this license
  document.

4. Combined Works.

You may convey a Combined Work under terms of your choice that, taken
together, effectively do not restrict modification of the portions of
the Library contained in the Combined Work and reverse engineering for
debugging such modifications, if you also do each of the following:

  a) Give prominent notice with each copy of the Combined Work that
  the Library is used in it and that the Library and its use are
  covered by this License.

  b) Accompany the Combined Work with a copy of the GNU GPL and this license
  document.

  c) For a Combined Work that displays copyright notices during
  execution, include the copyright notice for the Library among
  these notices, as well as a reference directing the user to the
  copies of the GNU GPL and this license document.

  d) Do one of the following:

      0) Convey the Minimal Corresponding Source under the terms of this
      License, and the Corresponding Application Code in a form
      suitable for, and under terms that permit, the user to
      recombine or relink the Application with a modified version of
      the Linked Version to produce a modified Combined Work, in the
      manner specified by section 6 of the GNU GPL for conveying
      Corresponding Source.

      1) Use a suitable shared library mechanism for linking with the
      Library.  A suitable mechanism is one that (a) uses at run time
      a copy of the Library already present on the user's computer
      system, and (b) will operate properly with a modified version
      of the Library that is interface-compatible with the Linked
      Version.

  e) Provide Installation Information, but only if you would otherwise
  be required to provide such information under section 6 of the
  GNU GPL, and only to the extent that such information is
  necessary to install and execute a modified version of the
  Combined Work produced by recombining or relinking the
  Application with a modified version of the Linked Version. (If
  you use option 4d0, the Installation Information must accompany
  the Minimal Corresponding Source and Corresponding Application
  Code. If you use option 4d1, you must provide the Installation
  Information in the manner specified by section 6 of the GNU GPL
  for conveying Corresponding Source.)

5. Combined Libraries.

You may place library facilities that are a work based on the
Library side by side in a single library together with other library
facilities that are not Applications and are not covered by this
License, and convey such a combined library under terms of your
choice, if you do both of the following:

  a) Accompany the combined library with a copy of the same work based
  on the Library, uncombined with any other library facilities,
  conveyed under the terms of this License.

  b) Give prominent notice with the combined library that part of it
  is a work based on the Library, and explaining where to find the
  accompanying uncombined form of the same work.

6. Revised Versions of the GNU Lesser General Public License.

The Free Software Foundation may publish revised and/or new versions
of the GNU Lesser General Public License from time to time. Such new
versions will be similar in spirit to the present version, but may
differ in detail to address new problems or concerns.

Each version is given a distinguishing version number. If the
Library as you received it specifies that a certain numbered version
of the GNU Lesser General Public License "or any later version"
applies to it, you have the option of following the terms and
conditions either of that published version or of any later version
published by the Free Software Foundation. If the Library as you
received it does not specify a version number of the GNU Lesser
General Public License, you may choose any version of the GNU Lesser
General Public License ever published by the Free Software Foundation.

If the Library as you received it specifies that a proxy can decide
whether future versions of the GNU Lesser General Public License shall
apply, that proxy's public statement of acceptance of any version is
permanent authorization for you to choose that version for the
Library.
"""

APACHE_2_TEXT = """\
Apache License
Version 2.0, January 2004
http://www.apache.org/licenses/

TERMS AND CONDITIONS FOR USE, REPRODUCTION, AND DISTRIBUTION

1. Definitions.
"License" shall mean the terms and conditions for use, reproduction,
and distribution as defined by Sections 1 through 9 of this document.

"Licensor" shall mean the copyright owner or entity authorized by
the copyright owner that is granting the License.

"Legal Entity" shall mean the union of the acting entity and all
other entities that control, are controlled by, or are under common
control with that entity. For the purposes of this definition,
"control" means (i) the power, direct or indirect, to cause the
direction or management of such entity, whether by contract or
otherwise, or (ii) ownership of fifty percent (50%) or more of the
outstanding shares, or (iii) beneficial ownership of such entity.

"You" (or "Your") shall mean an individual or Legal Entity
exercising permissions granted by this License.

"Source" form shall mean the preferred form for making modifications,
including but not limited to software source code, documentation
source, and configuration files.

"Object" form shall mean any form resulting from mechanical
transformation or translation of a Source form, including but
not limited to compiled object code, generated documentation,
and conversions to other media types.

"Work" shall mean the work of authorship, whether in Source or
Object form, made available under the License, as indicated by a
copyright notice that is included in or attached to the work.

"Derivative Works" shall mean any work, whether in Source or Object
form, that is based on (or derived from) the Work and for which the
editorial revisions, annotations, elaborations, or other modifications
represent, as a whole, an original work of authorship. For the purposes
of this License, Derivative Works shall not include works that remain
separable from, or merely link (or bind by name) to the interfaces of,
the Work and Derivative Works thereof.

"Contribution" shall mean any work of authorship, including
the original version of the Work and any modifications or additions
to that Work or Derivative Works thereof, that is intentionally
submitted to Licensor for inclusion in the Work by the copyright owner
or by an individual or Legal Entity authorized to submit on behalf of
the copyright owner. For the purposes of this definition, "submitted"
means any form of electronic, verbal, or written communication sent
to the Licensor or its representatives, including but not limited to
communication on electronic mailing lists, source code control systems,
and issue tracking systems that are managed by, or on behalf of, the
Licensor for the purpose of discussing and improving the Work, but
excluding communication that is conspicuously marked or otherwise
designated in writing by the copyright owner as "Not a Contribution."

"Contributor" shall mean Licensor and any individual or Legal Entity
on behalf of whom a Contribution has been received by Licensor and
subsequently incorporated within the Work.

2. Grant of Copyright License. Subject to the terms and conditions of
this License, each Contributor hereby grants to You a perpetual,
worldwide, non-exclusive, no-charge, royalty-free, irrevocable
copyright license to reproduce, prepare Derivative Works of,
publicly display, publicly perform, sublicense, and distribute the
Work and such Derivative Works in Source or Object form.

3. Grant of Patent License. Subject to the terms and conditions of
this License, each Contributor hereby grants to You a perpetual,
worldwide, non-exclusive, no-charge, royalty-free, irrevocable
(except as stated in this section) patent license to make, have made,
use, offer to sell, sell, import, and otherwise transfer the Work,
where such license applies only to those patent claims licensable
by such Contributor that are necessarily infringed by their
Contribution(s) alone or by combination of their Contribution(s)
with the Work to which such Contribution(s) was submitted. If You
institute patent litigation against any entity (including a
cross-claim or counterclaim in a lawsuit) alleging that the Work
or a Contribution incorporated within the Work constitutes direct
or contributory patent infringement, then any patent licenses
granted to You under this License for that Work shall terminate
as of the date such litigation is filed.

4. Redistribution. You may reproduce and distribute copies of the
Work or Derivative Works thereof in any medium, with or without
modifications, and in Source or Object form, provided that You
meet the following conditions:

(a) You must give any other recipients of the Work or
    Derivative Works a copy of this License; and

(b) You must cause any modified files to carry prominent notices
    stating that You changed the files; and

(c) You must retain, in the Source form of any Derivative Works
    that You distribute, all copyright, patent, trademark, and
    attribution notices from the Source form of the Work,
    excluding those notices that do not pertain to any part of
    the Derivative Works; and

(d) If the Work includes a "NOTICE" text file as part of its
    distribution, then any Derivative Works that You distribute must
    include a readable copy of the attribution notices contained
    within such NOTICE file, excluding those notices that do not
    pertain to any part of the Derivative Works, in at least one
    of the following places: within a NOTICE text file distributed
    as part of the Derivative Works; within the Source form or
    documentation, if provided along with the Derivative Works; or,
    within a display generated by the Derivative Works, if and
    wherever such third-party notices normally appear. The contents
    of the NOTICE file are for informational purposes only and
    do not modify the License. You may add Your own attribution
    notices within Derivative Works that You distribute, alongside
    or as an addendum to the NOTICE text from the Work, provided
    that such additional attribution notices cannot be construed
    as modifying the License.

You may add Your own copyright statement to Your modifications and
may provide additional or different license terms and conditions
for use, reproduction, or distribution of Your modifications, or
for any such Derivative Works as a whole, provided Your use,
reproduction, and distribution of the Work otherwise complies with
the conditions stated in this License.

5. Submission of Contributions. Unless You explicitly state otherwise,
any Contribution intentionally submitted for inclusion in the Work
by You to the Licensor shall be under the terms and conditions of
this License, without any additional terms or conditions.
Notwithstanding the above, nothing herein shall supersede or modify
the terms of any separate license agreement you may have executed
with Licensor regarding such Contributions.

6. Trademarks. This License does not grant permission to use the trade
names, trademarks, service marks, or product names of the Licensor,
except as required for reasonable and customary use in describing the
origin of the Work and reproducing the content of the NOTICE file.

7. Disclaimer of Warranty. Unless required by applicable law or
agreed to in writing, Licensor provides the Work (and each
Contributor provides its Contributions) on an "AS IS" BASIS,
WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or
implied, including, without limitation, any warranties or conditions
of TITLE, NON-INFRINGEMENT, MERCHANTABILITY, or FITNESS FOR A
PARTICULAR PURPOSE. You are solely responsible for determining the
appropriateness of using or redistributing the Work and assume any
risks associated with Your exercise of permissions under this License.

8. Limitation of Liability. In no event and under no legal theory,
whether in tort (including negligence), contract, or otherwise,
unless required by applicable law (such as deliberate and grossly
negligent acts) or agreed to in writing, shall any Contributor be
liable to You for damages, including any direct, indirect, special,
incidental, or exemplary damages of any character arising as a
result of this License or out of the use or inability to use the
Work (including but not limited to damages for loss of goodwill,
work stoppage, computer failure or malfunction, or any and all
other commercial damages or losses), even if such Contributor
has been advised of the possibility of such damages.

9. Accepting Warranty or Additional Liability. While redistributing
the Work or Derivative Works thereof, You may choose to offer,
and charge a fee for, acceptance of support, warranty, indemnity,
or other liability obligations and/or rights consistent with this
License. However, in accepting such obligations, You may act only
on Your own behalf and on Your sole responsibility, not on behalf
of any other Contributor, and only if You agree to indemnify,
defend, and hold each Contributor harmless for any liability
incurred by, or claims asserted against, such Contributor by reason
of your accepting any such warranty or additional liability.
"""

BSD_3_CLAUSE_TEMPLATE = """\
Redistribution and use in source and binary forms, with or without
modification, are permitted provided that the following conditions are
met:

1. Redistributions of source code must retain the above copyright
   notice, this list of conditions and the following disclaimer.

2. Redistributions in binary form must reproduce the above copyright
   notice, this list of conditions and the following disclaimer in the
   documentation and/or other materials provided with the distribution.

3. Neither the name of the copyright holder nor the names of its
   contributors may be used to endorse or promote products derived from
   this software without specific prior written permission.

THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS
"AS IS" AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT
LIMITED TO, THE IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR
A PARTICULAR PURPOSE ARE DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT
HOLDER OR CONTRIBUTORS BE LIABLE FOR ANY DIRECT, INDIRECT, INCIDENTAL,
SPECIAL, EXEMPLARY, OR CONSEQUENTIAL DAMAGES (INCLUDING, BUT NOT
LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR SERVICES; LOSS OF USE,
DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER CAUSED AND ON ANY
THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY, OR TORT
(INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE
OF THIS SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.
"""

MIT_TEMPLATE = """\
Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
"""

ZLIB_LICENSE = """\
Copyright (C) 1995-2024 Jean-loup Gailly and Mark Adler

This software is provided 'as-is', without any express or implied
warranty.  In no event will the authors be held liable for any damages
arising from the use of this software.

Permission is granted to anyone to use this software for any purpose,
including commercial applications, and to alter it and redistribute it
freely, subject to the following restrictions:

1. The origin of this software must not be misrepresented; you must not
   claim that you wrote the original software. If you use this software
   in a product, an acknowledgment in the product documentation would be
   appreciated but is not required.
2. Altered source versions must be plainly marked as such, and must not be
   misrepresented as being the original software.
3. This notice may not be removed or altered from any source distribution.

Jean-loup Gailly        Mark Adler
jloup@gzip.org          madler@alumni.caltech.edu
"""


def _get_python_license() -> str:
    """Extract Python PSF-2.0 license using the standard library."""
    try:
        buf = io.StringIO()
        old_stdout = sys.stdout
        sys.stdout = buf
        try:
            license()
        finally:
            sys.stdout = old_stdout
        out = buf.getvalue().strip()
        if out:
            return out
    except Exception:
        pass
    return "PSF LICENSE AGREEMENT FOR PYTHON (see https://docs.python.org/3/license.html)"


def _get_dist_license(dist_name: str) -> str:
    """Read license text from an installed distribution metadata."""
    try:
        dist = metadata.distribution(dist_name)
    except Exception:
        return ""

    # Check dist-info files for LICENSE / COPYING
    for file in (dist.files or []):
        name = file.name.upper()
        if name in ("LICENSE", "LICENSE.TXT", "COPYING", "COPYING.TXT"):
            try:
                content = dist.read_text(str(file))
                if content:
                    return content.strip()
            except Exception:
                continue
    return ""


def generate_notices(output_path: Path | None = None) -> str:
    """Generate the complete THIRD-PARTY-NOTICES.txt content."""
    sections = []

    # Header
    sections.append(
        "================================================================================\n"
        "PaperAssist Third-Party Software Notices and Licenses\n"
        "================================================================================\n\n"
        "PaperAssist is distributed under the GNU General Public License v3.0 or later\n"
        "(GPL-3.0-or-later). The standalone application bundle contains runtime libraries\n"
        "and components created by third parties, distributed under their respective\n"
        "open-source licenses as documented below.\n"
    )

    # 1. Python / CPython Runtime
    py_ver = sys.version.split()[0]
    sections.append(
        "--------------------------------------------------------------------------------\n"
        f"Python / CPython Runtime ({py_ver})\n"
        "License: Python Software Foundation License Version 2 (PSF-2.0)\n"
        "Website: https://www.python.org/\n"
        "--------------------------------------------------------------------------------\n\n"
        + _get_python_license()
        + "\n"
    )

    # 2. Qt for Python (PySide6 & shiboken6) and Qt 6 Runtime Libraries
    pyside_ver = "unknown"
    try:
        pyside_ver = metadata.version("PySide6")
    except Exception:
        try:
            pyside_ver = metadata.version("PySide6-Essentials")
        except Exception:
            pass

    sections.append(
        "--------------------------------------------------------------------------------\n"
        f"Qt for Python (PySide6 {pyside_ver}), shiboken6, and Qt 6 Shared Libraries\n"
        "License: GNU Lesser General Public License version 3 (LGPL-3.0-only)\n"
        "Website: https://www.qt.io/ / https://wiki.qt.io/Qt_for_Python\n"
        "--------------------------------------------------------------------------------\n\n"
        "PaperAssist dynamically links to Qt 6 and Qt for Python shared libraries\n"
        "(including libQt6Core, libQt6Gui, libQt6Widgets, libQt6DBus, libQt6OpenGL,\n"
        "libQt6Svg, libQt6WaylandClient, libQt6WlShellIntegration, libQt6XcbQpa,\n"
        "libpyside6, and libshiboken6).\n\n"
        "In accordance with Section 4 of the GNU Lesser General Public License (LGPL v3):\n"
        "- Users are free to modify or replace these Qt shared libraries located in the\n"
        "  bundle directory (_internal/ and _internal/PySide6/Qt/lib/) and run PaperAssist\n"
        "  with the modified libraries.\n"
        "- Source code for Qt 6 is available from the Qt Project at:\n"
        "    https://code.qt.io/cgit/qt/qtbase.git/\n"
        "- Source code for PySide6 is available at:\n"
        "    https://code.qt.io/cgit/pyside/pyside-setup.git/\n\n"
        + LGPL_3_TEXT.strip()
        + "\n"
    )

    # 3. PDFium and pypdfium2
    pdfium2_ver = "unknown"
    try:
        pdfium2_ver = metadata.version("pypdfium2")
    except Exception:
        pass

    pdfium2_lic = _get_dist_license("pypdfium2")
    if not pdfium2_lic:
        pdfium2_lic = (
            "pypdfium2 is licensed under Apache-2.0 or BSD-3-Clause at your option.\n\n"
            + APACHE_2_TEXT.strip()
            + "\n\n"
            + BSD_3_CLAUSE_TEMPLATE.strip()
        )

    sections.append(
        "--------------------------------------------------------------------------------\n"
        f"PDFium via pypdfium2 ({pdfium2_ver})\n"
        "License: Apache-2.0 / BSD-3-Clause\n"
        "Website: https://pdfium.googlesource.com/pdfium/ / https://github.com/pypdfium2-team/pypdfium2\n"
        "--------------------------------------------------------------------------------\n\n"
        "PDFium is Google's open-source PDF rendering engine. The prebuilt binary\n"
        "libpdfium.so is distributed via pypdfium2 and links/bundles several components.\n"
        "Detailed individual build notices for all PDFium build dependencies (Abseil,\n"
        "AGG, fast_float, FreeType, ICU, LCMS, libjpeg-turbo, libopenjpeg, libpng, simdutf,\n"
        "zlib) are provided in the bundle payload at:\n"
        "  _internal/pypdfium2-*.dist-info/licenses/data/linux_x64/BUILD_LICENSES/\n\n"
        + pdfium2_lic
        + "\n"
    )

    # 4. pypdf
    pypdf_ver = "unknown"
    try:
        pypdf_ver = metadata.version("pypdf")
    except Exception:
        pass

    pypdf_lic = _get_dist_license("pypdf")
    if not pypdf_lic:
        pypdf_lic = (
            "Copyright (c) 2006-2008, Mathieu Fenniak\n"
            "Some contributions copyright (c) 2007, Ashish Kulkarni\n"
            "Some contributions copyright (c) 2014, Steve Witham\n\n"
            + BSD_3_CLAUSE_TEMPLATE.strip()
        )

    sections.append(
        "--------------------------------------------------------------------------------\n"
        f"pypdf ({pypdf_ver})\n"
        "License: BSD-3-Clause\n"
        "Website: https://github.com/py-pdf/pypdf\n"
        "--------------------------------------------------------------------------------\n\n"
        + pypdf_lic
        + "\n"
    )

    # 5. Bundled System & C/C++ Libraries
    system_notices = """\
--------------------------------------------------------------------------------
Bundled System and C/C++ Shared Libraries
--------------------------------------------------------------------------------

The PaperAssist standalone bundle packages specific runtime support libraries to
ensure standalone execution across diverse Linux distributions:

1. SQLite (libsqlite3)
   Status: Public Domain (https://www.sqlite.org/copyright.html)
   All of the code and documentation in SQLite has been dedicated to the public domain.

2. OpenSSL (libcrypto, libssl)
   License: Apache License 2.0 (https://www.openssl.org/source/license.html)
   Copyright 1998-2024 The OpenSSL Project Authors.

3. zlib (libz)
   License: zlib License (https://zlib.net/zlib_license.html)
   Copyright (C) 1995-2024 Jean-loup Gailly and Mark Adler.

4. bzip2 (libbz2)
   License: BSD-like (https://sourceware.org/bzip2/)
   Copyright (C) 1996-2019 Julian Seward.

5. XZ / liblzma
   License: Public Domain / 0BSD (https://tukaani.org/xz/)
   Original author: Lasse Collin.

6. Zstandard (libzstd)
   License: BSD-3-Clause (https://github.com/facebook/zstd)
   Copyright (c) 2016-present, Facebook, Inc.

7. GLib & GThread (libglib-2.0, libgthread-2.0)
   License: GNU Lesser General Public License version 2.1 or later (LGPL-2.1-or-later)
   Copyright (C) 1995-2024 GLib Team and contributors.

8. D-Bus (libdbus-1)
   License: Academic Free License version 2.1 / GPL-2.0-or-later
   Copyright (C) 2002, 2003, 2004 Red Hat Inc.

9. Expat XML Parser (libexpat)
   License: MIT License (https://libexpat.github.io/)
   Copyright (c) 1998-2000 Thai Open Source Software Center Ltd and Clark Cooper.
   Copyright (c) 2001-2024 Expat contributors.

10. libffi
    License: MIT License (https://sourceware.org/libffi/)
    Copyright (c) 1996-2024 Anthony Green, Red Hat, Inc. and contributors.

11. Fontconfig (libfontconfig)
    License: HPND / MIT-like (https://www.freedesktop.org/wiki/Software/fontconfig/)
    Copyright (C) 2000, 2001, 2002, 2003 Keith Packard.

12. FreeType (libfreetype)
    License: The FreeType Project License / FTL (https://www.freetype.org/)
    Copyright (C) 1996-2024 David Turner, Robert Wilhelm, and Werner Lemberg.

13. HarfBuzz (libharfbuzz)
    License: Old MIT / HPND (https://harfbuzz.github.io/)
    Copyright (C) 2010, 2011, 2012 Google, Inc.; Behdad Esfahbod.

14. Graphite2 (libgraphite2)
    License: GNU Lesser General Public License version 2.1 or later (LGPL-2.1-or-later)
    Copyright (C) 2010 SIL International.

15. libpng (libpng16)
    License: libpng License (http://www.libpng.org/pub/png/src/libpng-LICENSE.txt)
    Copyright (c) 1995-2024 The PNG Reference Library Authors.

16. ICU - International Components for Unicode (libicudata, libicui18n, libicuuc)
    License: Unicode-DFS-2016 / ICU License (https://icu.unicode.org/)
    Copyright (c) 1991-2024 Unicode, Inc.

17. PCRE2 (libpcre2-8)
    License: BSD-3-Clause (https://www.pcre.org/)
    Copyright (c) 1997-2024 University of Cambridge.

18. mpdecimal (libmpdec)
    License: BSD-2-Clause (http://www.bytereef.org/mpdecimal/)
    Copyright (c) 2008-2024 Stefan Krah.

19. Brotli (libbrotlicommon, libbrotlidec)
    License: MIT License (https://github.com/google/brotli)
    Copyright (c) 2009, 2010, 2013-2016 by the Brotli Authors.

20. libxml2
    License: MIT License (https://gitlab.gnome.org/GNOME/libxml2)
    Copyright (C) 1998-2024 Daniel Veillard and contributors.

21. xkbcommon & xkbcommon-x11
    License: MIT / HPND (https://xkbcommon.org/)
    Copyright (C) 2009-2024 Daniel Stone, Ran Benita and contributors.

22. X11 & XCB Client Libraries (libX11, libXau, libXext, libxcb, libxcb-*)
    License: MIT / X11 (https://www.x.org/)
    Copyright (C) The Open Group, Keith Packard, Jamey Sharp, and contributors.

23. GCC Runtime Libraries (libgcc_s, libstdc++)
    License: GNU General Public License v3.0 with GCC Runtime Library Exception
    Copyright (C) Free Software Foundation, Inc.
    Under the GCC Runtime Library Exception, you have permission to propagate
    a work of Target Code formed by combining the Runtime Library with Independent Modules.

24. GNU Readline (libreadline)
    License: GNU General Public License v3.0 or later (GPL-3.0-or-later)
    Copyright (C) Free Software Foundation, Inc.

25. ncurses / terminfo (libtinfo)
    License: MIT-like / X11 (https://invisible-island.net/ncurses/)
    Copyright (c) 1998-2024 Free Software Foundation, Inc.

26. util-linux libuuid
    License: BSD-3-Clause (https://github.com/util-linux/util-linux)
    Copyright (C) 1996, 1997, 1998 Theodore Ts'o.
"""
    sections.append(system_notices)

    content = "\n".join(sections)
    if output_path is not None:
        out = Path(output_path)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(content, encoding="utf-8")
    return content


def generate_provenance(bundle_dir: Path, repo_root: Path | None = None) -> dict:
    """Generate BUILD-PROVENANCE.json recording build environment, commit, and ELF audit."""
    if repo_root is None:
        repo_root = ROOT

    bundle_dir = Path(bundle_dir)

    # 1. Version
    version = "unknown"
    try:
        from paperassist import __version__ as version
    except Exception:
        init_file = repo_root / "src" / "paperassist" / "__init__.py"
        if init_file.is_file():
            m = re.search(r'__version__\s*=\s*["\']([^"\']+)["\']', init_file.read_text(encoding="utf-8"))
            if m:
                version = m.group(1)

    # 2. Git commit & dirty status
    git_commit = "unknown"
    git_dirty = False
    git_dirty_count = 0
    git_dirty_files = []
    try:
        out = subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=str(repo_root),
            text=True,
            stderr=subprocess.DEVNULL,
        ).strip()
        if out:
            git_commit = out
    except Exception:
        pass

    try:
        status_out = subprocess.check_output(
            ["git", "status", "--porcelain"],
            cwd=str(repo_root),
            text=True,
            stderr=subprocess.DEVNULL,
        ).splitlines()
        dirty_lines = [line.strip() for line in status_out if line.strip()]
        git_dirty_count = len(dirty_lines)
        git_dirty = git_dirty_count > 0
        git_dirty_files = dirty_lines
    except Exception:
        pass

    # 3. Host platform & OS release & glibc
    build_platform = platform.platform()

    os_release_data = {}
    for os_release_path in (Path("/etc/os-release"), Path("/usr/lib/os-release")):
        if os_release_path.is_file():
            try:
                for line in os_release_path.read_text(encoding="utf-8").splitlines():
                    if "=" in line:
                        k, v = line.split("=", 1)
                        os_release_data[k.strip()] = v.strip().strip('"\'')
                break
            except Exception:
                pass

    os_release = {
        "PRETTY_NAME": os_release_data.get("PRETTY_NAME", platform.system()),
        "ID": os_release_data.get("ID", platform.system().lower()),
        "VERSION_ID": os_release_data.get("VERSION_ID", platform.release()),
    }

    host_glibc = "unknown"
    try:
        host_glibc = os.confstr("CS_GNU_LIBC_VERSION")
    except Exception:
        if sys.platform.startswith("linux"):
            try:
                import ctypes
                libc = ctypes.CDLL(None)
                gnu_get_libc_version = libc.gnu_get_libc_version
                gnu_get_libc_version.restype = ctypes.c_char_p
                host_glibc = f"glibc {gnu_get_libc_version().decode('ascii')}"
            except Exception:
                pass

    python_version = sys.version.split()[0]

    # 4. Empirical ELF audit across bundle
    bootloader_max_glibc = None
    bundle_max_glibc = None
    bundle_max_glibc_files = []
    bundle_max_glibcxx = None
    bundle_max_cxxabi = None
    external_shared_libraries = []

    if sys.platform.startswith("linux") and bundle_dir.is_dir():
        glibc_pattern = re.compile(r"GLIBC_([0-9]+)\.([0-9]+)")
        glibcxx_pattern = re.compile(r"GLIBCXX_([0-9]+)\.([0-9]+)(?:\.([0-9]+))?")
        cxxabi_pattern = re.compile(r"CXXABI_([0-9]+)\.([0-9]+)(?:\.([0-9]+))?")
        needed_pattern = re.compile(r"\(NEEDED\)\s+.*?:?\s*\[([^\]]+)\]")

        env = os.environ.copy()
        env["LC_ALL"] = "C"

        # Scan PaperAssist executable for bootloader_max_glibc
        exe_path = bundle_dir / "PaperAssist"
        if exe_path.is_file():
            try:
                out = subprocess.check_output(
                    ["readelf", "-V", str(exe_path)],
                    env=env,
                    text=True,
                    errors="replace",
                    stderr=subprocess.DEVNULL,
                )
                exe_glibc = set()
                for m in glibc_pattern.finditer(out):
                    exe_glibc.add((int(m.group(1)), int(m.group(2))))
                if exe_glibc:
                    top = sorted(exe_glibc)[-1]
                    bootloader_max_glibc = f"GLIBC_{top[0]}.{top[1]}"
            except Exception:
                pass

        # Scan all ELF files in bundle
        bundled_filenames = set()
        elf_files = []
        for r, dirs, files in os.walk(bundle_dir):
            for f in files:
                bundled_filenames.add(f)
                p = Path(r) / f
                if not p.is_symlink():
                    try:
                        with p.open("rb") as fp:
                            if fp.read(4) == b"\x7fELF":
                                elf_files.append(p)
                    except Exception:
                        pass

        all_needed = set()
        file_glibc = {}
        all_glibcxx = set()
        all_cxxabi = set()

        for p in elf_files:
            rel_p = str(p.relative_to(bundle_dir)).replace("\\", "/")
            try:
                # Version symbols
                out_v = subprocess.check_output(
                    ["readelf", "-V", str(p)],
                    env=env,
                    text=True,
                    errors="replace",
                    stderr=subprocess.DEVNULL,
                )
                g_vers = set()
                for m in glibc_pattern.finditer(out_v):
                    g_vers.add((int(m.group(1)), int(m.group(2))))
                if g_vers:
                    file_glibc[rel_p] = sorted(g_vers)[-1]

                for m in glibcxx_pattern.finditer(out_v):
                    maj = int(m.group(1))
                    min_ = int(m.group(2))
                    pat = int(m.group(3)) if m.group(3) else 0
                    all_glibcxx.add((maj, min_, pat, m.group(0)))

                for m in cxxabi_pattern.finditer(out_v):
                    maj = int(m.group(1))
                    min_ = int(m.group(2))
                    pat = int(m.group(3)) if m.group(3) else 0
                    all_cxxabi.add((maj, min_, pat, m.group(0)))

                # Dynamic dependencies (DT_NEEDED)
                out_d = subprocess.check_output(
                    ["readelf", "-d", str(p)],
                    env=env,
                    text=True,
                    errors="replace",
                    stderr=subprocess.DEVNULL,
                )
                for m in needed_pattern.finditer(out_d):
                    all_needed.add(m.group(1))
            except Exception:
                pass

        if file_glibc:
            max_ver = max(file_glibc.values())
            bundle_max_glibc = f"GLIBC_{max_ver[0]}.{max_ver[1]}"
            bundle_max_glibc_files = sorted(
                rel for rel, ver in file_glibc.items() if ver == max_ver
            )

        if all_glibcxx:
            bundle_max_glibcxx = sorted(all_glibcxx)[-1][3]

        if all_cxxabi:
            bundle_max_cxxabi = sorted(all_cxxabi)[-1][3]

        external_shared_libraries = sorted(
            lib for lib in all_needed if lib not in bundled_filenames
        )

    provenance = {
        "version": version,
        "git_commit": git_commit,
        "git_dirty": git_dirty,
        "git_dirty_count": git_dirty_count,
        "git_dirty_files": git_dirty_files,
        "build_platform": build_platform,
        "os_release": os_release,
        "host_glibc": host_glibc,
        "python_version": python_version,
        "bootloader_max_glibc": bootloader_max_glibc,
        "bundle_max_glibc": bundle_max_glibc,
        "bundle_max_glibc_files": bundle_max_glibc_files,
        "bundle_max_glibcxx": bundle_max_glibcxx,
        "bundle_max_cxxabi": bundle_max_cxxabi,
        "external_shared_libraries": external_shared_libraries,
    }

    prov_json = json.dumps(provenance, indent=2) + "\n"
    if bundle_dir.is_dir():
        (bundle_dir / "BUILD-PROVENANCE.json").write_text(prov_json, encoding="utf-8")
        internal_dir = bundle_dir / "_internal"
        if internal_dir.is_dir():
            (internal_dir / "BUILD-PROVENANCE.json").write_text(prov_json, encoding="utf-8")

    return provenance


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Generate third-party notices and build provenance")
    parser.add_argument("target", nargs="?", default=None, help="Target path for notices")
    parser.add_argument("--provenance-bundle", type=Path, default=None, help="Bundle directory to inspect and generate BUILD-PROVENANCE.json for")
    args = parser.parse_args()

    if args.provenance_bundle:
        prov = generate_provenance(args.provenance_bundle, ROOT)
        print(f"Generated BUILD-PROVENANCE.json for {args.provenance_bundle} (bundle max glibc: {prov.get('bundle_max_glibc')})")

    target = Path(args.target) if args.target else ROOT / "THIRD-PARTY-NOTICES.txt"
    generate_notices(target)
    print(f"Generated notices in {target}")
