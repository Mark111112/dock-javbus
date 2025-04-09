import os
import sys
import logging
import shutil
import json

# 配置日志
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

def setup_environment():
    """设置应用环境，创建必要的目录和文件"""
    try:
        # 确定应用程序路径
        if getattr(sys, 'frozen', False):
            # 运行在打包环境
            if hasattr(sys, '_MEIPASS'):
                # PyInstaller的_MEIPASS是临时目录
                application_path = os.path.dirname(sys.executable)
            else:
                application_path = os.path.dirname(sys.executable)
        else:
            # 运行在开发环境
            application_path = os.path.dirname(os.path.abspath(__file__))
        
        # 确保工作目录设置正确
        os.chdir(application_path)
        logging.info(f"应用路径: {application_path}")
        logging.info(f"当前工作目录: {os.getcwd()}")
        
        # 创建视频相关目录
        video_dirs = [
            'buspic',
            'buspic/actor',
            'buspic/covers',
            'downloads',  # 用于保存下载的视频
            'temp'
        ]
        
        for directory in video_dirs:
            dir_path = os.path.join(application_path, directory)
            os.makedirs(dir_path, exist_ok=True)
            logging.info(f"确保目录存在: {dir_path}")

        # 确保配置文件存在
        config_file = os.path.join(application_path, 'config.json')
        if not os.path.exists(config_file):
            # 如果在打包环境中，从资源文件复制配置
            if hasattr(sys, '_MEIPASS'):
                source_file = os.path.join(sys._MEIPASS, 'config.json')
                if os.path.exists(source_file):
                    shutil.copy2(source_file, config_file)
                    logging.info(f"从资源复制配置文件: {source_file} -> {config_file}")
                else:
                    # 创建默认配置
                    default_config = {
                        "fanza_mapping": {},
                        "proxy": {
                            "enabled": False,
                            "host": "",
                            "port": ""
                        },
                        "download_path": "downloads",
                        "language": "zh_CN"
                    }
                    with open(config_file, 'w', encoding='utf-8') as f:
                        json.dump(default_config, f, ensure_ascii=False, indent=4)
                    logging.info(f"创建默认配置文件: {config_file}")
            else:
                # 创建默认配置
                default_config = {
                    "fanza_mapping": {},
                    "proxy": {
                        "enabled": False,
                        "host": "",
                        "port": ""
                    },
                    "download_path": "downloads",
                    "language": "zh_CN"
                }
                with open(config_file, 'w', encoding='utf-8') as f:
                    json.dump(default_config, f, ensure_ascii=False, indent=4)
                logging.info(f"创建默认配置文件: {config_file}")
            
        # 确保数据库文件存在
        db_file = os.path.join(application_path, 'javbus_data.db')
        if not os.path.exists(db_file):
            # 创建一个空的SQLite数据库
            import sqlite3
            conn = sqlite3.connect(db_file)
            logging.info(f"创建新的数据库文件: {db_file}")
            
            # 创建必要的表
            cursor = conn.cursor()
            
            # 创建演员表
            cursor.execute('''
            CREATE TABLE IF NOT EXISTS stars (
                id TEXT PRIMARY KEY,
                name TEXT,
                data TEXT,
                last_updated INTEGER
            )
            ''')
            
            # 创建影片表
            cursor.execute('''
            CREATE TABLE IF NOT EXISTS movies (
                id TEXT PRIMARY KEY,
                title TEXT,
                data TEXT,
                last_updated INTEGER
            )
            ''')
            
            # 创建搜索历史表
            cursor.execute('''
            CREATE TABLE IF NOT EXISTS search_history (
                keyword TEXT PRIMARY KEY,
                last_search INTEGER
            )
            ''')
            
            conn.commit()
            conn.close()
            logging.info("数据库表创建完成")
        
        # 确保video_player2.py存在（用于影片播放功能）
        player_file = os.path.join(application_path, 'video_player2.py')
        if not os.path.exists(player_file) and hasattr(sys, '_MEIPASS'):
            # 从资源文件复制
            source_file = os.path.join(sys._MEIPASS, 'video_player2.py')
            if os.path.exists(source_file):
                shutil.copy2(source_file, player_file)
                logging.info(f"从资源复制视频播放器模块: {source_file} -> {player_file}")
            else:
                # 尝试从其他可能的位置查找
                logging.warning(f"找不到源视频播放器模块: {source_file}")
                # 打印PyInstaller临时目录中的文件列表，用于调试
                logging.info(f"PyInstaller临时目录中的文件: {os.listdir(sys._MEIPASS)}")
                
                # 尝试查找文件
                for root, dirs, files in os.walk(sys._MEIPASS):
                    if 'video_player2.py' in files:
                        source_file = os.path.join(root, 'video_player2.py')
                        shutil.copy2(source_file, player_file)
                        logging.info(f"找到并复制视频播放器模块: {source_file} -> {player_file}")
                        break
                else:
                    logging.error("无法找到video_player2.py，播放功能可能无法正常工作！")
            
    except Exception as e:
        logging.error(f"设置环境时出错: {str(e)}")
        import traceback
        logging.error(traceback.format_exc())

# 程序启动时立即执行
setup_environment() 