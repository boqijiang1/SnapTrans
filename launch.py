"""启动器：PyInstaller 打包入口 / 开机自启入口。

保证无论从哪个工作目录启动，snaptrans 包都能被找到。
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from snaptrans.app import main

if __name__ == "__main__":
    sys.exit(main())
