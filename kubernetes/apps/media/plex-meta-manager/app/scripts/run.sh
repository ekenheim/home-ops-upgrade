#!/usr/bin/env bash

pmm () {
    echo "*** pmm: $1 ***"

    python3 plex_meta_manager.py \
        --run \
        --read-only-config \
        --run-libraries "$1"
}

pmm "Movies"
pmm "Movies 4k"
pmm "TV Shows"
pmm "TV Shows 4k"
pmm "Cartoons"
