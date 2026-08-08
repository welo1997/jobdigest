# Postgres 16 + pgvector, built on ALPINE on purpose.
#
# The obvious move is `image: pgvector/pgvector:pg16`, and it would silently corrupt this
# database. That image is Debian/glibc; ours is Alpine/musl; and the cluster was initdb'd with
# `collate=en_US.utf8`, a locale-dependent collation. The two libcs implement that same name
# differently — measured 2026-08-08 on both images:
#
#     musl  :  'a' < 'B'  ->  false   sorted: Apple,Banana,apple,banana,cherry   (byte order)
#     glibc :  'a' < 'B'  ->  true    sorted: apple,Apple,banana,Banana,cherry   (dictionary)
#
# Point an existing data directory at the other libc and every B-tree index on a text column
# is still physically ordered by the OLD rules while the server compares by the NEW ones. Index
# scans then miss rows that exist, with no error anywhere. There are 34 indexes in `public` and
# 19 indexed text/varchar columns, including the unique index on `profiles.email` — so the
# failure mode includes "a duplicate subscription slips through" and "a manage-token lookup
# finds nothing". That is this repo's recurring shape at its worst: wrong answers, green checks.
#
# Staying on musl keeps the data directory bit-compatible, so switching to this image is an
# ordinary container recreate rather than a dump/restore with downtime.
#
# `with_llvm=no` skips the LLVM bitcode step, which is only a JIT-inlining optimisation and is
# what would otherwise drag clang/llvm (hundreds of MB) into a build on a 2-vCPU box. pgvector
# works identically without it; only PG's JIT inlining of its functions is lost, and this
# workload is index lookups, not JIT-heavy analytics.
FROM postgres:16-alpine

ARG PGVECTOR_REF=v0.8.1

RUN apk add --no-cache --virtual .build-deps build-base git \
 && git clone --branch "$PGVECTOR_REF" --depth 1 https://github.com/pgvector/pgvector.git /tmp/pgvector \
 && cd /tmp/pgvector \
 && make with_llvm=no OPTFLAGS="" \
 && make install with_llvm=no \
 && cd / \
 && rm -rf /tmp/pgvector \
 && apk del .build-deps
