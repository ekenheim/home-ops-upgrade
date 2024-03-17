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
pmm "Series"
pmm "Series 4k"
pmm "Cartoons"
