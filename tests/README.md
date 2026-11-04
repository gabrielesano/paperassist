# Tests

Use focused unittest tests for library integrity, deterministic metadata, citation
analysis and API behavior. Generated fixtures use temporary directories. The private
PDF corpus stays outside Git. Browser checks exercise real PDF.js rendering and
annotation save/reopen paths rather than replacing them with mocks.
