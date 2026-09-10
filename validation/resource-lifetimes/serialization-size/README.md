# SQLite serialized image sizing

The probe passes 15 cases on each pinned Python runtime: page sizes 512,
4096 and 65536, each with an empty table, inserts, deletes, vacuum and a
serialized/deserialized connection. In these cases, page_count multiplied by
page_size equals both the serialized image length and a backup file length.
The JSON files record the actual Python and SQLite versions. These finite
cases do not establish an allocator or concurrency bound.

SQLite documents page_count as the number of pages in the database file and
page_size as the page size in bytes. It describes serialization of an in-memory
database as the bytes that would be written if backed up to disk:

- https://www.sqlite.org/pragma.html#pragma_page_count
- https://www.sqlite.org/pragma.html#pragma_page_size
- https://www.sqlite.org/c3ref/serialize.html

Reviewed 2026-09-10. Together these contracts support using the product as the
image size for the private, unchanged main database. Implementation must validate
the returned integers, page-size range and power of two, positive page count,
and the existing MAX_BYTES limit before allocating the image. No database writes
may occur between measurement and serialization. Cursors must close explicitly.
The returned length should also be checked as a consistency assertion; checking
after serialization alone is not admission.

The pinned CPython copy review is retained in ../cipher-buffer-capability/README.md.
Admission must cover the possible native serialized image and simultaneous Python
bytes result. Transferring to an alias-tracked payload introduces another copy;
the temporary result must be cleared before releasing its reservation, including
failure paths and retained tracebacks. The owned output must remain charged through
publication and any surviving aliases. Budget rejection must precede serialize.

This proposed accounting covers image payload capacity, not SQLite page caches,
query allocations, deserialization storage or cryptographic internal scratch.
No runtime behavior has changed in this evidence commit. Required regression
coverage includes rejection before serialization, copy overlap, publication
failure, cancellation, retained aliases and output format compatibility.
