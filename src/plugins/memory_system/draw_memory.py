# -*- coding: utf-8 -*-
import os
import sys
import jieba
import networkx as nx
import matplotlib.pyplot as plt
import math
from collections import Counter
import datetime
import random
import time
from dotenv import load_dotenv
import sys
import asyncio
import aiohttp
from typing import Tuple

sys.path.append("C:/GitHub/MaiMBot")  # 添加项目根目录到 Python 路径
from src.common.database import Database  # 使用正确的导入语法

# 加载.env.dev文件
env_path = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(__file__)))), '.env.dev')
load_dotenv(env_path)

class LLMModel:
    def __init__(self, model_name=os.getenv("SILICONFLOW_MODEL_V3"), **kwargs):
        self.model_name = model_name
        self.params = kwargs
        self.api_key = os.getenv("SILICONFLOW_KEY")
        self.base_url = os.getenv("SILICONFLOW_BASE_URL")

    async def generate_response(self, prompt: str) -> Tuple[str, str]:
        """根据输入的提示生成模型的响应"""
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json"
        }
        
        # 构建请求体
        data = {
            "model": self.model_name,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.5,
            **self.params
        }
        
        # 发送请求到完整的chat/completions端点
        api_url = f"{self.base_url.rstrip('/')}/chat/completions"
        
        max_retries = 3
        base_wait_time = 15
        
        for retry in range(max_retries):
            try:
                async with aiohttp.ClientSession() as session:
                    async with session.post(api_url, headers=headers, json=data) as response:
                        if response.status == 429:
                            wait_time = base_wait_time * (2 ** retry)  # 指数退避
                            print(f"遇到请求限制(429)，等待{wait_time}秒后重试...")
                            await asyncio.sleep(wait_time)
                            continue
                            
                        response.raise_for_status()  # 检查其他响应状态
                        
                        result = await response.json()
                        if "choices" in result and len(result["choices"]) > 0:
                            content = result["choices"][0]["message"]["content"]
                            reasoning_content = result["choices"][0]["message"].get("reasoning_content", "")
                            return content, reasoning_content
                        return "没有返回结果", ""
                
            except Exception as e:
                if retry < max_retries - 1:  # 如果还有重试机会
                    wait_time = base_wait_time * (2 ** retry)
                    print(f"请求失败，等待{wait_time}秒后重试... 错误: {str(e)}")
                    await asyncio.sleep(wait_time)
                else:
                    return f"请求失败: {str(e)}", ""
        
        return "达到最大重试次数，请求仍然失败", ""

   
class Memory_graph:
    def __init__(self):
        self.G = nx.Graph()  # 使用 networkx 的图结构
        self.db = Database.get_instance()
        
    def connect_dot(self, concept1, concept2):
        self.G.add_edge(concept1, concept2)
    
    def add_dot(self, concept, memory):
        if concept in self.G:
            # 如果节点已存在，将新记忆添加到现有列表中
            if 'memory_items' in self.G.nodes[concept]:
                if not isinstance(self.G.nodes[concept]['memory_items'], list):
                    # 如果当前不是列表，将其转换为列表
                    self.G.nodes[concept]['memory_items'] = [self.G.nodes[concept]['memory_items']]
                self.G.nodes[concept]['memory_items'].append(memory)
            else:
                self.G.nodes[concept]['memory_items'] = [memory]
        else:
            # 如果是新节点，创建新的记忆列表
            self.G.add_node(concept, memory_items=[memory])
        
    def get_dot(self, concept):
        # 检查节点是否存在于图中
        if concept in self.G:
            # 从图中获取节点数据
            node_data = self.G.nodes[concept]
            # print(node_data)
            # 创建新的Memory_dot对象
            return concept,node_data
        return None

    def get_related_item(self, topic, depth=1):
        if topic not in self.G:
            return [], []
            
        first_layer_items = []
        second_layer_items = []
        
        # 获取相邻节点
        neighbors = list(self.G.neighbors(topic))
        # print(f"第一层: {topic}")
        
        # 获取当前节点的记忆项
        node_data = self.get_dot(topic)
        if node_data:
            concept, data = node_data
            if 'memory_items' in data:
                memory_items = data['memory_items']
                if isinstance(memory_items, list):
                    first_layer_items.extend(memory_items)
                else:
                    first_layer_items.append(memory_items)
        
        # 只在depth=2时获取第二层记忆
        if depth >= 2:
            # 获取相邻节点的记忆项
            for neighbor in neighbors:
                # print(f"第二层: {neighbor}")
                node_data = self.get_dot(neighbor)
                if node_data:
                    concept, data = node_data
                    if 'memory_items' in data:
                        memory_items = data['memory_items']
                        if isinstance(memory_items, list):
                            second_layer_items.extend(memory_items)
                        else:
                            second_layer_items.append(memory_items)
        
        return first_layer_items, second_layer_items
    
    def store_memory(self):
        for node in self.G.nodes():
            dot_data = {
                "concept": node
            }
            self.db.db.store_memory_dots.insert_one(dot_data)
    
    @property
    def dots(self):
        # 返回所有节点对应的 Memory_dot 对象
        return [self.get_dot(node) for node in self.G.nodes()]
    
    
    def get_random_chat_from_db(self, length: int, timestamp: str):
        # 从数据库中根据时间戳获取离其最近的聊天记录
        chat_text = ''
        closest_record = self.db.db.messages.find_one({"time": {"$lte": timestamp}}, sort=[('time', -1)])  # 调试输出
        print(f"距离time最近的消息时间: {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(int(closest_record['time'])))}")
        
        if closest_record:
            closest_time = closest_record['time']
            group_id = closest_record['group_id']  # 获取groupid
            # 获取该时间戳之后的length条消息，且groupid相同
            chat_record = list(self.db.db.messages.find({"time": {"$gt": closest_time}, "group_id": group_id}).sort('time', 1).limit(length))
            for record in chat_record:
                time_str = time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(int(record['time'])))
                try:
                    displayname="[(%s)%s]%s" % (record["user_id"],record["user_nickname"],record["user_cardname"])
                except:
                    displayname=record["user_nickname"] or "用户" + str(record["user_id"])
                chat_text += f'[{time_str}] {displayname}: {record["processed_plain_text"]}\n'  # 添加发送者和时间信息
            return chat_text
        
        return []  # 如果没有找到记录，返回空列表

    def save_graph_to_db(self):
        # 清空现有的图数据
        self.db.db.graph_data.delete_many({})
        # 保存节点
        for node in self.G.nodes(data=True):
            node_data = {
                'concept': node[0],
                'memory_items': node[1].get('memory_items', [])  # 默认为空列表
            }
            self.db.db.graph_data.nodes.insert_one(node_data)
        # 保存边
        for edge in self.G.edges():
            edge_data = {
                'source': edge[0],
                'target': edge[1]
            }
            self.db.db.graph_data.edges.insert_one(edge_data)

    def load_graph_from_db(self):
        # 清空当前图
        self.G.clear()
        # 加载节点
        nodes = self.db.db.graph_data.nodes.find()
        for node in nodes:
            memory_items = node.get('memory_items', [])
            if not isinstance(memory_items, list):
                memory_items = [memory_items] if memory_items else []
            self.G.add_node(node['concept'], memory_items=memory_items)
        # 加载边
        edges = self.db.db.graph_data.edges.find()
        for edge in edges:
            self.G.add_edge(edge['source'], edge['target'])


def main():
    # 初始化数据库
    Database.initialize(
        host=os.getenv("MONGODB_HOST", "127.0.0.1"),
        port=int(os.getenv("MONGODB_PORT", "27017")),
        db_name=os.getenv("DATABASE_NAME", "MegBot"),
        username=os.getenv("MONGODB_USERNAME", ""),
        password=os.getenv("MONGODB_PASSWORD", ""),
        auth_source=os.getenv("MONGODB_AUTH_SOURCE", "")
    )
    
    memory_graph = Memory_graph()
    # 创建LLM模型实例

    memory_graph.load_graph_from_db()
    # 展示两种不同的可视化方式
    print("\n按连接数量着色的图谱：")
    # visualize_graph(memory_graph, color_by_memory=False)
    visualize_graph_lite(memory_graph, color_by_memory=False)   
    
    print("\n按记忆数量着色的图谱：")
    # visualize_graph(memory_graph, color_by_memory=True)
    visualize_graph_lite(memory_graph, color_by_memory=True)
    
    # memory_graph.save_graph_to_db()
    
    while True:
        query = input("请输入新的查询概念（输入'退出'以结束）：")
        if query.lower() == '退出':
            break
        first_layer_items, second_layer_items = memory_graph.get_related_item(query)
        if first_layer_items or second_layer_items:
            print("\n第一层记忆：")
            for item in first_layer_items:
                print(item)
            print("\n第二层记忆：")
            for item in second_layer_items:
                print(item)
        else:
            print("未找到相关记忆。")
            

def segment_text(text):
    seg_text = list(jieba.cut(text))
    return seg_text    

def find_topic(text, topic_num):
    prompt = f'这是一段文字：{text}。请你从这段话中总结出{topic_num}个话题，帮我列出来，用逗号隔开，尽可能精简。只需要列举{topic_num}个话题就好，不要告诉我其他内容。'
    return prompt

def topic_what(text, topic):
    prompt = f'这是一段文字：{text}。我想知道这记忆里有什么关于{topic}的话题，帮我总结成一句自然的话，可以包含时间和人物。只输出这句话就好'
    return prompt

def visualize_graph(memory_graph: Memory_graph, color_by_memory: bool = False):
    # 设置中文字体
    plt.rcParams['font.sans-serif'] = ['SimHei']  # 用来正常显示中文标签
    plt.rcParams['axes.unicode_minus'] = False  # 用来正常显示负号
    
    G = memory_graph.G
    
    # 保存图到本地
    nx.write_gml(G, "memory_graph.gml")  # 保存为 GML 格式

    # 根据连接条数或记忆数量设置节点颜色
    node_colors = []
    nodes = list(G.nodes())  # 获取图中实际的节点列表
    
    if color_by_memory:
        # 计算每个节点的记忆数量
        memory_counts = []
        for node in nodes:
            memory_items = G.nodes[node].get('memory_items', [])
            if isinstance(memory_items, list):
                count = len(memory_items)
            else:
                count = 1 if memory_items else 0
            memory_counts.append(count)
        max_memories = max(memory_counts) if memory_counts else 1
        
        for count in memory_counts:
            # 使用不同的颜色方案：红色表示记忆多，蓝色表示记忆少
            if max_memories > 0:
                intensity = min(1.0, count / max_memories)
                color = (intensity, 0, 1.0 - intensity)  # 从蓝色渐变到红色
            else:
                color = (0, 0, 1)  # 如果没有记忆，则为蓝色
            node_colors.append(color)
    else:
        # 使用原来的连接数量着色方案
        max_degree = max(G.degree(), key=lambda x: x[1])[1] if G.degree() else 1
        for node in nodes:
            degree = G.degree(node)
            if max_degree > 0:
                red = min(1.0, degree / max_degree)
                blue = 1.0 - red
                color = (red, 0, blue)
            else:
                color = (0, 0, 1)
            node_colors.append(color)
    
    # 绘制图形
    plt.figure(figsize=(12, 8))
    pos = nx.spring_layout(G, k=1, iterations=50)
    nx.draw(G, pos, 
           with_labels=True, 
           node_color=node_colors,
           node_size=2000,
           font_size=10,
           font_family='SimHei',
           font_weight='bold')
    
    title = '记忆图谱可视化 - ' + ('按记忆数量着色' if color_by_memory else '按连接数量着色')
    plt.title(title, fontsize=16, fontfamily='SimHei')
    plt.show()


def visualize_graph_lite(memory_graph: Memory_graph, color_by_memory: bool = False):
    # 设置中文字体
    plt.rcParams['font.sans-serif'] = ['SimHei']  # 用来正常显示中文标签
    plt.rcParams['axes.unicode_minus'] = False  # 用来正常显示负号
    
    G = memory_graph.G
    
    # 创建一个新图用于可视化
    H = G.copy()
    
    # 移除只有一条记忆的节点和连接数少于3的节点
    nodes_to_remove = []
    for node in H.nodes():
        memory_items = H.nodes[node].get('memory_items', [])
        memory_count = len(memory_items) if isinstance(memory_items, list) else (1 if memory_items else 0)
        degree = H.degree(node)
        if memory_count <= 2 or degree <= 2:
            nodes_to_remove.append(node)
    
    H.remove_nodes_from(nodes_to_remove)
    
    # 如果过滤后没有节点，则返回
    if len(H.nodes()) == 0:
        print("过滤后没有符合条件的节点可显示")
        return
    
    # 保存图到本地
    nx.write_gml(H, "memory_graph.gml")  # 保存为 GML 格式

    # 根据连接条数或记忆数量设置节点颜色
    node_colors = []
    nodes = list(H.nodes())  # 获取图中实际的节点列表
    
    if color_by_memory:
        # 计算每个节点的记忆数量
        memory_counts = []
        for node in nodes:
            memory_items = H.nodes[node].get('memory_items', [])
            if isinstance(memory_items, list):
                count = len(memory_items)
            else:
                count = 1 if memory_items else 0
            memory_counts.append(count)
        max_memories = max(memory_counts) if memory_counts else 1
        
        for count in memory_counts:
            # 使用不同的颜色方案：红色表示记忆多，蓝色表示记忆少
            if max_memories > 0:
                intensity = min(1.0, count / max_memories)
                color = (intensity, 0, 1.0 - intensity)  # 从蓝色渐变到红色
            else:
                color = (0, 0, 1)  # 如果没有记忆，则为蓝色
            node_colors.append(color)
    else:
        # 使用原来的连接数量着色方案
        max_degree = max(H.degree(), key=lambda x: x[1])[1] if H.degree() else 1
        for node in nodes:
            degree = H.degree(node)
            if max_degree > 0:
                red = min(1.0, degree / max_degree)
                blue = 1.0 - red
                color = (red, 0, blue)
            else:
                color = (0, 0, 1)
            node_colors.append(color)
    
    # 绘制图形
    plt.figure(figsize=(12, 8))
    pos = nx.spring_layout(H, k=1, iterations=50)
    nx.draw(H, pos, 
           with_labels=True, 
           node_color=node_colors,
           node_size=2000,
           font_size=10,
           font_family='SimHei',
           font_weight='bold')
    
    title = '记忆图谱可视化 - ' + ('按记忆数量着色' if color_by_memory else '按连接数量着色')
    plt.title(title, fontsize=16, fontfamily='SimHei')
    plt.show()
    
    
    
if __name__ == "__main__":
    main()