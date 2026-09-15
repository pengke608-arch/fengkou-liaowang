#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
风口瞭望 - 数据自动更新脚本
=========================
从各种公开数据源抓取最新数据，整理后写入 data/*.json 文件。

数据源：
- 国务院政策文件库 (gov.cn)
- 各地方政府政策公告
- 36氪 / 虎嗅等科技媒体 RSS（行业趋势）
- 远程工作招聘信息
- B站视频信息（通过 API）

使用方法：
    python3 scripts/update_data.py

注意：
- 脚本具有容错性，单个数据源失败不会影响整体运行
- 抓取失败时保留现有数据不覆盖
- 所有操作都有日志输出
"""

import os
import sys
import json
import time
import logging
import datetime
import traceback
from typing import List, Dict, Optional

# ============ 配置 ============

# 脚本所在目录
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
# 项目根目录
ROOT_DIR = os.path.dirname(SCRIPT_DIR)
# 数据目录
DATA_DIR = os.path.join(ROOT_DIR, 'data')

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger(__name__)

# ============ 工具函数 ============

def load_json(filename: str) -> Optional[list]:
    """加载 JSON 数据文件，失败返回 None"""
    filepath = os.path.join(DATA_DIR, filename)
    try:
        if os.path.exists(filepath):
            with open(filepath, 'r', encoding='utf-8') as f:
                return json.load(f)
        logger.warning(f'文件不存在: {filename}')
        return None
    except Exception as e:
        logger.error(f'加载 {filename} 失败: {e}')
        return None


def get_item_key(item: Dict) -> str:
    """获取数据项的唯一键，用于去重（基于标题）"""
    # 优先使用标题中文作为去重键
    title = item.get('title', {})
    if isinstance(title, dict):
        return title.get('cn', '') or title.get('en', '')
    elif isinstance(title, str):
        return title
    # 视频用 bvid 或 url
    if item.get('bvid'):
        return item['bvid']
    if item.get('url'):
        return item['url']
    # 城市用 id
    if item.get('id'):
        return item['id']
    return str(item)


def merge_data(old_data: List[Dict], new_data: List[Dict], max_items: int = 20) -> List[Dict]:
    """
    合并新旧数据，新数据在前，去重
    :param old_data: 原有数据
    :param new_data: 新抓取的数据
    :param max_items: 最大保留条数
    :return: 合并后的数据
    """
    if not old_data:
        return new_data[:max_items]
    if not new_data:
        return old_data[:max_items]
    
    merged = []
    seen = set()
    
    # 先加新数据
    for item in new_data:
        key = get_item_key(item)
        if key and key not in seen:
            seen.add(key)
            merged.append(item)
    
    # 再加旧数据中不重复的
    for item in old_data:
        key = get_item_key(item)
        if key and key not in seen:
            seen.add(key)
            merged.append(item)
    
    return merged[:max_items]


def save_json(filename: str, data: list) -> bool:
    """保存数据到 JSON 文件，成功返回 True"""
    filepath = os.path.join(DATA_DIR, filename)
    try:
        # 先写入临时文件，再替换，避免写入中断导致数据损坏
        temp_path = filepath + '.tmp'
        with open(temp_path, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(temp_path, filepath)
        logger.info(f'已保存 {filename} ({len(data)} 条记录)')
        return True
    except Exception as e:
        logger.error(f'保存 {filename} 失败: {e}')
        # 清理临时文件
        temp_path = filepath + '.tmp'
        if os.path.exists(temp_path):
            try:
                os.remove(temp_path)
            except:
                pass
        return False


def safe_fetch(url: str, timeout: int = 15) -> Optional[str]:
    """安全的 HTTP 请求，失败返回 None"""
    try:
        import urllib.request
        import urllib.error
        import ssl
        
        # 创建不验证 SSL 的上下文（部分政府网站证书有问题）
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
            'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8',
        }
        
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=timeout, context=ctx) as response:
            if response.status == 200:
                # 处理编码
                content_type = response.headers.get('Content-Type', '')
                if 'charset=' in content_type:
                    charset = content_type.split('charset=')[-1].split(';')[0].strip()
                else:
                    charset = 'utf-8'
                return response.read().decode(charset, errors='replace')
            else:
                logger.warning(f'HTTP {response.status}: {url}')
                return None
    except Exception as e:
        logger.warning(f'请求失败 {url}: {e}')
        return None


def parse_rss(xml_content: str) -> List[Dict]:
    """简单的 RSS 解析器，返回文章列表"""
    import re
    
    items = []
    # 匹配 <item> 标签
    item_pattern = re.compile(r'<item>(.*?)</item>', re.DOTALL | re.IGNORECASE)
    title_pattern = re.compile(r'<title>(.*?)</title>', re.DOTALL | re.IGNORECASE)
    link_pattern = re.compile(r'<link>(.*?)</link>', re.DOTALL | re.IGNORECASE)
    desc_pattern = re.compile(r'<description>(.*?)</description>', re.DOTALL | re.IGNORECASE)
    pubdate_pattern = re.compile(r'<pubDate>(.*?)</pubDate>', re.DOTALL | re.IGNORECASE)
    
    for item_match in item_pattern.finditer(xml_content):
        item_xml = item_match.group(1)
        
        def extract(pattern, default=''):
            m = pattern.search(item_xml)
            if m:
                # 去除 CDATA
                text = m.group(1).strip()
                text = re.sub(r'<!\[CDATA\[(.*?)\]\]>', r'\1', text, flags=re.DOTALL)
                # 去除 HTML 标签
                text = re.sub(r'<[^>]+>', '', text)
                return text.strip()
            return default
        
        item = {
            'title': extract(title_pattern),
            'link': extract(link_pattern),
            'desc': extract(desc_pattern),
            'pubDate': extract(pubdate_pattern),
        }
        if item['title']:
            items.append(item)
    
    return items


def update_last_update(source: str = 'auto'):
    """更新 last-update.json"""
    data = {
        'time': datetime.datetime.now(datetime.timezone.utc).isoformat(),
        'source': source
    }
    filepath = os.path.join(DATA_DIR, 'last-update.json')
    try:
        with open(filepath, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        logger.info(f'已更新 last-update.json ({source})')
    except Exception as e:
        logger.error(f'更新 last-update.json 失败: {e}')


# ============ 数据源抓取函数 ============

def fetch_gov_policies() -> Optional[List[Dict]]:
    """
    抓取国务院政策文件
    数据源：国务院政策文件库 RSS / 各地方政府门户
    """
    logger.info('开始抓取政策数据...')
    
    policies = []
    
    # 国务院政策文件库 - 最新政策
    rss_sources = [
        {
            'name': '国务院最新政策',
            'url': 'http://www.gov.cn/zhengce/zuixin.htm',
            'cat': 'Gov',
            'region': {'cn': '全国', 'en': 'National'},
            'is_rss': False  # 是 HTML 页面
        },
    ]
    
    # 尝试抓取国务院 RSS（如果有的话）
    gov_rss_urls = [
        'http://www.gov.cn/rss/zhengce.xml',
        'http://www.gov.cn/rss/govt.xml',
    ]
    
    fetched_count = 0
    
    for rss_url in gov_rss_urls:
        try:
            content = safe_fetch(rss_url, timeout=10)
            if content and '<item>' in content:
                items = parse_rss(content)
                for item in items[:8]:  # 每个源取前8条
                    policy = {
                        'cat': 'Gov',
                        'tag': 'gov',
                        'region': {'cn': '全国', 'en': 'National'},
                        'title': {
                            'cn': item['title'][:80],
                            'en': item['title'][:80]  # 英文标题暂用中文代替
                        },
                        'desc': {
                            'cn': item['desc'][:200] if item['desc'] else '',
                            'en': ''
                        },
                        'links': [
                            {'t': {'cn': '政府官网', 'en': 'Gov'}, 'u': item['link'], 'gov': 1}
                        ]
                    }
                    policies.append(policy)
                fetched_count += len(items[:8])
                logger.info(f'  ✓ {rss_url}: {len(items[:8])} 条')
                break  # 一个成功就够了
        except Exception as e:
            logger.warning(f'  ✗ {rss_url}: {e}')
            continue
    
    # 如果 RSS 都失败了，尝试抓取 HTML 页面
    if fetched_count == 0:
        logger.info('  RSS 不可用，尝试抓取 HTML 页面...')
        try:
            html = safe_fetch('http://www.gov.cn/zhengce/zuixin.htm', timeout=15)
            if html:
                import re
                # 简单提取政策列表（根据实际页面结构调整）
                items = re.findall(
                    r'<li>.*?<a[^>]+href="([^"]+)"[^>]*>(.*?)</a>.*?<span[^>]*>(.*?)</span>',
                    html, re.DOTALL
                )
                for url, title, date in items[:10]:
                    title = re.sub(r'<[^>]+>', '', title).strip()
                    if not title:
                        continue
                    if url.startswith('/'):
                        url = 'http://www.gov.cn' + url
                    policy = {
                        'cat': 'Gov',
                        'tag': 'gov',
                        'region': {'cn': '全国', 'en': 'National'},
                        'title': {'cn': title[:80], 'en': title[:80]},
                        'desc': {'cn': date.strip(), 'en': ''},
                        'links': [{'t': {'cn': '政府官网', 'en': 'Gov'}, 'u': url, 'gov': 1}]
                    }
                    policies.append(policy)
                logger.info(f'  ✓ HTML 抓取: {len(policies)} 条')
        except Exception as e:
            logger.warning(f'  ✗ HTML 抓取失败: {e}')
    
    if policies:
        logger.info(f'政策数据抓取完成，共 {len(policies)} 条')
        return policies
    else:
        logger.warning('政策数据抓取失败，将保留现有数据')
        return None


def fetch_tech_trends() -> Optional[List[Dict]]:
    """
    抓取行业趋势数据
    数据源：36氪、虎嗅等科技媒体 RSS
    """
    logger.info('开始抓取行业趋势数据...')
    
    trends = []
    
    rss_sources = [
        {
            'name': '36氪',
            'url': 'https://36kr.com/feed',
            'cat': 'AI',
            'tag': 'hot'
        },
        {
            'name': '虎嗅网',
            'url': 'https://www.huxiu.com/rss/0.xml',
            'cat': 'Media',
            'tag': 'new'
        },
        {
            'name': '爱范儿', 
            'url': 'https://www.ifanr.com/feed',
            'cat': 'AI',
            'tag': 'new'
        },
    ]
    
    fetched_sources = 0
    
    for source in rss_sources:
        try:
            content = safe_fetch(source['url'], timeout=10)
            if not content:
                logger.warning(f'  ✗ {source["name"]}: 请求失败')
                continue
            
            items = parse_rss(content)
            if not items:
                logger.warning(f'  ✗ {source["name"]}: 解析失败')
                continue
            
            # 取前 3-4 条
            for item in items[:4]:
                trend = {
                    'cat': source['cat'],
                    'tag': source['tag'],
                    'title': {
                        'cn': item['title'][:60],
                        'en': item['title'][:60]
                    },
                    'desc': {
                        'cn': item['desc'][:150] if item['desc'] else '',
                        'en': ''
                    },
                    'nums': [],
                    'links': [
                        {'t': {'cn': source['name'], 'en': source['name']}, 'u': item['link']}
                    ]
                }
                trends.append(trend)
            
            fetched_sources += 1
            logger.info(f'  ✓ {source["name"]}: {len(items[:4])} 条')
            
        except Exception as e:
            logger.warning(f'  ✗ {source["name"]}: {e}')
            continue
    
    if trends:
        logger.info(f'行业趋势抓取完成，共 {len(trends)} 条 (来自 {fetched_sources} 个源)')
        return trends
    else:
        logger.warning('行业趋势抓取失败，将保留现有数据')
        return None


def fetch_global_news() -> Optional[List[Dict]]:
    """
    抓取国际趋势数据
    数据源：国际科技媒体 RSS
    """
    logger.info('开始抓取国际趋势数据...')
    
    global_data = []
    
    rss_sources = [
        {
            'name': 'TechCrunch',
            'url': 'https://techcrunch.com/feed/',
            'cat': 'AI',
        },
        {
            'name': 'The Verge',
            'url': 'https://www.theverge.com/rss/index.xml',
            'cat': 'AI',
        },
    ]
    
    for source in rss_sources:
        try:
            content = safe_fetch(source['url'], timeout=15)
            if not content:
                continue
            
            items = parse_rss(content)
            if not items:
                continue
            
            for item in items[:3]:
                entry = {
                    'cat': source['cat'],
                    'tag': 'global',
                    'title': {
                        'cn': item['title'][:80],
                        'en': item['title'][:80]
                    },
                    'desc': {
                        'cn': item['desc'][:150] if item['desc'] else '',
                        'en': item['desc'][:200] if item['desc'] else ''
                    },
                    'links': [
                        {'t': {'cn': source['name'], 'en': source['name']}, 'u': item['link']}
                    ]
                }
                global_data.append(entry)
            
            logger.info(f'  ✓ {source["name"]}: {len(items[:3])} 条')
            
        except Exception as e:
            logger.warning(f'  ✗ {source["name"]}: {e}')
            continue
    
    if global_data:
        logger.info(f'国际趋势抓取完成，共 {len(global_data)} 条')
        return global_data
    else:
        logger.warning('国际趋势抓取失败，将保留现有数据')
        return None


def fetch_remote_jobs() -> Optional[List[Dict]]:
    """
    抓取远程工作招聘信息
    数据源：电鸭社区、远程工作平台等
    """
    logger.info('开始抓取远程工作数据...')
    
    remote_jobs = []
    
    job_sources = [
        {
            'name': '电鸭社区',
            'url': 'https://eleduck.com/search?type=1&keyword=远程',
            'cat': 'Tech'
        },
    ]
    
    # 由于大多数招聘网站没有 RSS，这里作为占位
    # 实际部署时可以根据需要添加具体的抓取逻辑
    logger.info('  远程工作数据源需要根据实际网站结构定制')
    logger.info('  当前版本保留现有数据')
    
    # 返回 None 表示不更新，保留现有数据
    return None


def fetch_bilibili_videos() -> Optional[List[Dict]]:
    """
    抓取 B 站视频信息
    数据源：B 站搜索 API
    """
    logger.info('开始抓取视频数据...')
    
    videos = []
    
    # B 站搜索 API 关键词
    keywords = ['AI漫剧', 'AI副业', '远程工作', '行业风口']
    
    for keyword in keywords:
        try:
            # B 站搜索接口
            import urllib.parse
            search_url = f'https://api.bilibili.com/x/web-interface/search/type?search_type=video&keyword={urllib.parse.quote(keyword)}'
            content = safe_fetch(search_url, timeout=10)
            
            if not content:
                continue
            
            data = json.loads(content)
            if data.get('code') != 0:
                continue
            
            results = data.get('data', {}).get('result', [])
            for item in results[:3]:
                video = {
                    'bvid': item.get('bvid', ''),
                    'plat': 'bilibili',
                    'title': {
                        'cn': item.get('title', '').replace('<em class="keyword">', '').replace('</em>', '')[:60],
                        'en': ''
                    },
                    'views': {
                        'cn': str(item.get('play', 0)) + ' 播放',
                        'en': str(item.get('play', 0)) + ' views'
                    },
                    'blogger': {
                        'cn': item.get('author', ''),
                        'en': item.get('author', '')
                    },
                    'cat': 'AI',
                    'desc': {
                        'cn': item.get('description', '')[:100],
                        'en': ''
                    },
                    'cover': item.get('pic', ''),
                    'url': f'https://www.bilibili.com/video/{item.get("bvid", "")}'
                }
                videos.append(video)
            
            logger.info(f'  ✓ B站「{keyword}」: {min(len(results), 3)} 条')
            time.sleep(1)  # 避免请求过快
            
        except Exception as e:
            logger.warning(f'  ✗ B站「{keyword}」: {e}')
            continue
    
    if videos:
        logger.info(f'视频数据抓取完成，共 {len(videos)} 条')
        return videos
    else:
        logger.warning('视频数据抓取失败，将保留现有数据')
        return None


def fetch_city_data() -> Optional[List[Dict]]:
    """
    城市数据一般比较稳定，不需要频繁更新
    这里保留现有数据
    """
    logger.info('城市数据稳定，无需更新')
    return None


def fetch_remote_platforms() -> Optional[List[Dict]]:
    """
    远程工作平台数据相对稳定
    保留现有数据
    """
    logger.info('远程平台数据稳定，无需更新')
    return None


# ============ 主函数 ============

def main():
    """主更新流程"""
    logger.info('=' * 50)
    logger.info('风口瞭望 - 数据更新开始')
    logger.info('=' * 50)
    
    start_time = time.time()
    updated_files = []
    failed_files = []
    
    # 确保数据目录存在
    os.makedirs(DATA_DIR, exist_ok=True)
    
    # 定义需要更新的数据源
    update_tasks = [
        ('trends.json', fetch_tech_trends),
        ('policies.json', fetch_gov_policies),
        ('global.json', fetch_global_news),
        ('remote-jobs.json', fetch_remote_jobs),
        ('videos.json', fetch_bilibili_videos),
        ('cities.json', fetch_city_data),
        ('remote-platforms.json', fetch_remote_platforms),
    ]
    
    for filename, fetch_func in update_tasks:
        logger.info(f'\n--- 处理 {filename} ---')
        try:
            new_data = fetch_func()
            
            if new_data is not None and len(new_data) > 0:
                # 加载旧数据
                old_data = load_json(filename) or []
                # 合并新旧数据
                merged_data = merge_data(old_data, new_data)
                # 保存合并后的数据
                if save_json(filename, merged_data):
                    updated_files.append(filename)
                    logger.info(f'  合并完成: {len(old_data)}旧 + {len(new_data)}新 = {len(merged_data)}条')
                else:
                    failed_files.append(filename)
            else:
                # 抓取失败或返回 None，保留现有数据
                logger.info(f'  保留现有 {filename}')
                
        except Exception as e:
            logger.error(f'处理 {filename} 时发生异常: {e}')
            logger.debug(traceback.format_exc())
            failed_files.append(filename)
    
    # 更新时间戳
    update_last_update('auto-update')
    
    # 统计
    elapsed = time.time() - start_time
    logger.info('\n' + '=' * 50)
    logger.info(f'数据更新完成，耗时 {elapsed:.1f} 秒')
    logger.info(f'  成功更新: {len(updated_files)} 个文件')
    for f in updated_files:
        logger.info(f'    ✓ {f}')
    if failed_files:
        logger.info(f'  失败/跳过: {len(failed_files)} 个文件')
        for f in failed_files:
            logger.info(f'    ✗ {f}')
    logger.info('=' * 50)
    
    # 如果有任何一个成功更新，返回 0
    return 0 if updated_files else 1


if __name__ == '__main__':
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        logger.info('用户中断')
        sys.exit(1)
    except Exception as e:
        logger.critical(f'脚本异常退出: {e}')
        logger.debug(traceback.format_exc())
        sys.exit(1)
