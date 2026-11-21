import pandas as pd
import matplotlib.pyplot as plt
import matplotlib

# 解决图表中文显示乱码的问题
matplotlib.rcParams['font.sans-serif'] = ['SimHei']  # Windows 系统用黑体
matplotlib.rcParams['axes.unicode_minus'] = False

CSV_FILE = r"D:\下载\PythonProject4\爬虫\scrapling_c\video_analytics.csv"


def analyze_and_plot():
    # 1. 读取 CSV 数据
    print(f"📂 正在读取数据: {CSV_FILE}")
    try:
        df = pd.read_csv(CSV_FILE)
    except FileNotFoundError:
        print("❌ 找不到 CSV 文件，请先运行抓取程序！")
        return

    if df.empty:
        print("❌ CSV 里面没有数据。")
        return

    print(f"✅ 成功读取 {len(df)} 条视频数据。\n")
    print(df.head(), "\n")  # 打印前几行看看

    # ==========================================
    # 图表 1：点赞量 Top 10 水平柱状图
    # ==========================================
    # 按点赞量降序排列，取前 10
    top10 = df.nlargest(10, '点赞量')

    plt.figure(figsize=(10, 6))
    # 倒序排列，让最高的排在最上面
    plt.barh(top10['视频标题'].iloc[::-1], top10['点赞量'].iloc[::-1], color='skyblue')
    plt.xlabel('点赞量', fontsize=12)
    plt.title('点赞量 Top 10 视频', fontsize=15)
    plt.tight_layout()
    plt.show()

    # ==========================================
    # 图表 2：点赞量与评论量的散点图
    # ==========================================
    plt.figure(figsize=(8, 6))
    plt.scatter(df['点赞量'], df['评论量'], alpha=0.6, color='coral')
    plt.xlabel('点赞量', fontsize=12)
    plt.ylabel('评论量', fontsize=12)
    plt.title('点赞量与评论量相关性', fontsize=15)
    plt.grid(True, linestyle='--', alpha=0.5)
    plt.tight_layout()
    plt.show()

    # ==========================================
    # 图表 3：各作者平均点赞量对比（如果有多个作者）
    # ==========================================
    if len(df['作者昵称'].unique()) > 1:
        author_stats = df.groupby('作者昵称')[['点赞量', '评论量']].mean().sort_values(by='点赞量', ascending=False)

        author_stats.plot(kind='bar', figsize=(10, 6), color=['#4CAF50', '#FF9800'])
        plt.xlabel('作者昵称', fontsize=12)
        plt.ylabel('平均值', fontsize=12)
        plt.title('各作者平均点赞量与评论量对比', fontsize=15)
        plt.xticks(rotation=45)  # 旋转X轴标签防重叠
        plt.tight_layout()
        plt.show()


if __name__ == "__main__":
    analyze_and_plot()