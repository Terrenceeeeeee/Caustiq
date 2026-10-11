#!/usr/bin/env python3
"""Launch the Caustiq desktop application."""

from qt_app import CaustiqWindow, main, padded_data_limits

UnifiedCaustiqApp = CaustiqWindow

__all__ = ["CaustiqWindow", "UnifiedCaustiqApp", "padded_data_limits", "main"]


if __name__ == "__main__":
    main()
