#!/bin/bash
bash "$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)/runtime.sh" --ensure
