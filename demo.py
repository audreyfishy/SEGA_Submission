# はじめに
#   疑似コードをPythonベースで記載いたしました。
#   マッチメイキングの流れを把握していただけると幸いです。


# Global Variables: 
bot = discord.Client()
MATCHMAKING_CHANNEL_ID = # マッチメイキングを開始するチャンネルのID


# on_raw_reaction_add
#   マッチメイキングを開始するチャンネルにてプレイヤーがリアクションをしたときに実行される関数
#   NOTE: この関数はDiscord APIのイベントループ内で呼び出される
# Input: 
#   payload:=リアクション情報
# Outcome:
#   マッチメイキングが正常に終了すれば、データベースが更新される
@bot.event
async def on_raw_reaction_add(payload):
    # リアクションをしたプレイヤーの情報を取得
    player = bot.get_user(payload.user_id)
    # リアクションをしたプレイヤーがbotの場合は何もしない
    if player.bot: return
    # リアクションをしたプレイヤーがすでにマッチメイキング中の場合は何もしない
    if isDuringMatchmaking(player.id): return
    # リアクションが押されたチャンネルがマッチメイキングを開始するチャンネルでない場合は何もしない
    if payload.channel_id != MATCHMAKING_CHANNEL_ID: return

    # マッチメイキングスタート           
    try:
      # マッチメイキングキューに入ったことをプレイヤーに通知
      # マッチメイキング完了後、メッセージを削除するために、メッセージIDを保存しておく
      notiMatchMade = await notiMatchmakingStart(player.id)

      # player.idをマッチメイキングキュー(asyncio.Queue)に追加
      await pushPlayer(payload.user_id)
      time = 0

      # マッチメイキングの経過時間とキャンセルボタンを表示するメッセージをプレイヤーのDMへ送信
      dm = await player.send(embed = eQueue(str(time)+"秒", payload.user_id))
      await dm.add_reaction("❌")

      # プレイヤーが待機中にキャンセルボタンを押した場合に処理を行う関数
      def reaction_check(reaction, user):
          # リアクションをしたプレイヤーがbotの場合は何もしない
          if user.bot: return
          # isinstance: リアクションのチャンネルがDMチャンネルであるかを判定
          # reaction.message.channel == dm.id: BOTから送信されたメッセージであるかを判定
          # user == player: リアクションをしたプレイヤーがこのコルーチンのプレイヤーであるかを判定
          # reaction.emoji == "❌": リアクションがキャンセルボタンであるかを判定
          return isinstance(reaction.message.channel, discord.DMChannel)\
              and reaction.message.id == dm.id and\
              user == player and str(reaction.emoji) == "❌"                    

      # 五秒おきにマッチメイキングの経過時間を更新する
      # 五秒おきにレートの下限と上限が更新される -> レートの近いプレイヤーとマッチングする
      async def incTime(dm):
          time = 0 # マッチメイキングの経過時間
          _range = 100 # レートの下限と上限の差
          while True:
              # 5秒待機
              await asyncio.sleep(5)
              time += 5
              
              # 経過時間をパースしてメッセージを更新
              h, m, s = get_h_m_s(time)           
              if time < 60:
                  tstr = f"{s}秒"
              if time >= 60:
                  tstr = f"{m}分{s}秒"
              if time >= 60 * 60:
                  tstr = f"{h}時間{m}分{s}秒"

              # プレイヤーが他のプレイヤーによってsetから取り出されていなければ、経過時間をDMに反映
              if isSearching(payload): await dm.edit(embed = eQueue(tstr, payload.user_id))
              # 取り出されていれば、マッチメイキングを終了
              else: return False

              # 対象範囲にプレイヤーがいる場合、マッチングを開始する
              # ここでcheck関数はasyncio.Lock()を利用した排他制御を行っているため、
              # async関数であり、これによってマッチメイキングが正常に動作する
              # 対戦相手を見つけた場合、グローバル変数matchに対戦相手のIDを格納する
              if (await check(payload.user_id, _range)): return True

      # 対戦相手を探すタスクとキャンセルタスクを作成し、並列実行
      wait_reaction_task = asyncio.create_task(bot.wait_for\
          ("reaction_add", check = reaction_check), name = "wait_reaction")
      wait_time_task = asyncio.create_task(incTime(dm),name = "wait_time")
      aws = {wait_reaction_task, wait_time_task}
      done, _ = await asyncio.wait(aws, return_when = asyncio.FIRST_COMPLETED)              
      
      # キャンセルボタンが押され、並列実行が停止した場合、プレイヤーをマッチメイキングキューから削除
      if list(done)[0].get_name() == "wait_reaction":
          reaction, _ = wait_reaction_task.result()
          if str(reaction.emoji) == "❌":
            # マッチメイキングをキャンセルしたことをプレイヤーに通知
            embed=discord.Embed(title="❌", description="マッチメイキングをキャンセルしました。", color=0xff0000)
            await player.send(embed = embed)

      # マッチメイキングの経過時間とキャンセルボタンを表示するメッセージを削除
      await dm.delete() 
      # マッチメイキングキューに入ったことをプレイヤーに通知したメッセージを削除
      await notiMatchMade.delete()

    # マッチメイキング中にプレイヤーがサーバーを離脱した場合（コーナーケース）
    except discord.HTTPException:
        await cancelMatch(payload.user_id) # RoomHostからuserを削除し、マッチングを終了する
        return

    # このユーザーをルームホストとして、イベントループを継続
    if isRoomHost(payload.user_id) != -1:
        # 実際にはこの分岐内で
        # 1. ルームを作成
        # 2. ２人のユーザーをルームに入れる（DMに招待リンクを送信）
        # 3. 試合を行う
        # 4. リアクションにて結果報告を待つ
        # 5. 結果を集計し、レートを更新する
        # 6. ルームを削除する
        # 7. DMの招待リンクを削除する
        # 上記の処理を行うasync関数を実行しておりました
        await afterMatch(payload.user_id)