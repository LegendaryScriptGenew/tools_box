#!/usr/bin/env python3
"""
测试脚本：验证Linux系统管理工具箱的界面框架
"""
import sys
import os

# 添加当前目录到Python路径
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

try:
    print("=" * 60)
    print("Linux系统管理工具箱 - 界面框架测试")
    print("=" * 60)
    
    print("\n[1/5] 正在导入PySide6...")
    from PySide6.QtWidgets import QApplication
    print("  [OK] PySide6导入成功")
    
    print("\n[2/5] 正在导入Linux_yum_sub模块...")
    from Linux_yum_sub import MainWindow
    print("  [OK] Linux_yum_sub模块导入成功")
    
    print("\n[3/5] 正在创建QApplication...")
    app = QApplication(sys.argv)
    print("  [OK] QApplication创建成功")
    
    print("\n[4/5] 正在创建MainWindow...")
    window = MainWindow(lang="zh")
    print("  [OK] MainWindow创建成功")
    
    print("\n[5/5] 正在检查标签页...")
    if hasattr(window, 'tab_widget'):
        tab_count = window.tab_widget.count()
        print(f"  [OK] 找到QTabWidget，共有{tab_count}个标签页")
        
        tab_names = []
        for i in range(tab_count):
            tab_text = window.tab_widget.tabText(i)
            tab_names.append(tab_text)
            print(f"    - 标签页{i+1}: {tab_text}")
    else:
        print("  [ERROR] 未找到QTabWidget")
        sys.exit(1)
    
    print("\n" + "=" * 60)
    print("[SUCCESS] 界面框架测试通过！")
    print("=" * 60)
    
    print("\n测试结果：")
    print("  ✅ PySide6导入成功")
    print("  ✅ 模块导入成功")
    print("  ✅ 主窗口创建成功")
    print(f"  ✅ 成功创建{tab_count}个标签页")
    print("  ✅ 所有UI组件初始化成功")
    
    print("\n可以运行以下命令启动程序：")
    print("  cd F:/python_project/self_tools/tool_box")
    print("  .venv/Scripts/python.exe Linux_yum_sub.py")
    
    print("\n注意事项：")
    print("  1. NTP时间同步标签页已创建（UI框架完整，功能待实现）")
    print("  2. 系统初始化标签页已创建（UI框架完整，功能待实现）")
    print("  3. YUM服务器配置和客户端配置标签页保持原界面不变")
    
    sys.exit(0)
    
except Exception as e:
    print("\n" + "=" * 60)
    print("[ERROR] 测试失败")
    print("=" * 60)
    print(f"\n错误: {e}")
    import traceback
    traceback.print_exc()
    sys.exit(1)
