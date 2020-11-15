import requests
import json
import threading
import chess
import socket
import re
import time
import configparser
import random

header = ""
chatDict = {}
whitelist = { "tuxmania": ["tuxmania"]}
activeGames = []

class Game:

    def __init__(self, gameid):
        self.gameid = gameid

        self.configured = False
        self.twitch_channel = None
        self.user_time = None
        self.twitch_socket = None
        self.vote_time = 0
        self.b = None

        self.playGame()

    @staticmethod
    def cancelResignGame(gameid):
        # abort or resign game
        try:
            requests.post("https://lichess.org/api/bot/game/{}/abort".format(gameid), headers=header)
        except:
            pass

        try:
            requests.post("https://lichess.org/api/bot/game/{}/resign".format(gameid), headers=header)
        except:
            pass

    def sendMessage(self, message):

        # Message needs to be wrapped in special format for twitch
        messageTemp = "PRIVMSG #" + self.twitch_channel + " :" + message + "\n"

        # send message encoded as utf8
        self.twitch_socket.send(messageTemp.encode("utf-8"))

    def handleMove(self, move):

        # translation table for german pieces
        translationDictionary = {"S": "N", "L": "B", "D": "Q", "T": "R"}

        # decompose string into char list since we need to replace first letter
        string_list = list(move)

        # german to english translation
        if string_list[0] in translationDictionary:
            string_list[0] = translationDictionary[string_list[0]]

        # char array to string
        m = "".join(string_list)

        return m

    def getTwitchSocket(self):
        twitch_server = "irc.chat.twitch.tv"
        twitch_port = 6667
        twitch_nickname = "tuxchessbot"

        # connect to twitch chat
        sock = socket.socket()
        sock.connect((twitch_server, twitch_port))

        self.twitch_socket = sock

        sock.send(f"PASS {twitch_token}\n".encode('utf-8'))
        sock.send(f"NICK {twitch_nickname}\n".encode('utf-8'))

        twitch_channel2 = "#" + self.twitch_channel

        connectedMessage = "[tuxchess] Connected lichess game https://lichess.org/" + self.gameid
        self.sendMessage(connectedMessage)

        sock.send(f"JOIN {twitch_channel2}\n".encode('utf-8'))
        # sleep needed when account is not mod/vip on channel (timeout rule)
        time.sleep(1)
        return sock

    def startChatRead(self, twitch_socket, twitch_channel):
        twitch_socket.recv(2048).decode("utf-8")

        # reset old messages by assigning twitch channel chat to new list
        if twitch_channel not in chatDict:
            chatDict[twitch_channel] = list()

        # function will be called async, so loop is infinite
        while (True):
            msg = twitch_socket.recv(2048).decode("utf-8")

            username = re.search(r"\w+", msg).group(0)
            CHAT_MSG = re.compile(r"^:\w+!\w+@\w+\.tmi\.twitch\.tv PRIVMSG #\w+ :")

            message = CHAT_MSG.sub("", msg).rstrip('\n')

            chatDict[twitch_channel].append((username, message))

    def getMoveFromChat(self, board):

        moveDict = {}
        votedUsers = {}

        for entry in chatDict[self.twitch_channel]:

            username, message = entry
            # strip LF and CR
            message = message.replace("\r", "").replace("\n", "")
            message = self.handleMove(message)


            # try to parse message as UCI move ( e4e5)
            try:
                chess.Move.from_uci(message)
                move = message
            except:


                # if that didnt work try to apply the move on the current board
                # by using algebraic SAN moe "e4 Nf3 Qxb4"
                try:
                    move = board.push_san(message)
                    # if succeeded pop it from the board again
                    board.pop()
                except:

                    # if neither UCI nor SAN, it was not a legal move, go to next message
                    continue

            # check if move is legal
            test_board = board.copy()
            try:
                test_board.push(move)
            except:
                continue

            # each user can only vote once
            # last vote counts
            votedUsers[username] = move

        # evaluate most voted move
        for _, move in votedUsers.items():
            if move not in moveDict:
                moveDict[move] = 0
            moveDict[move] += 1

        # find out move with maximum votes
        max = 0
        maxCandidates = list()

        for move, count in moveDict.items():
            if count > max:
                max = count

        # get list of all max candidates
        for move, count in moveDict.items():
            if count == max:
                maxCandidates.append(move)

        multipleCandidates = len(maxCandidates) > 1

        if len(maxCandidates) == 0:
            return None, 0, False

        # pick random move from all max candidates
        maxmove = random.choice(maxCandidates)

        return maxmove, max, multipleCandidates


    def writeToLichessChat(self, message):
        msgjson = {"room": "player", "text": message}
        requests.post("https://lichess.org/api/bot/game/{}/chat".format(self.gameid), headers=header,
                           json=msgjson)

    def makeChatMove(self, lastMove):
        failcnt = 0
        move = None
        wasRandom = False

        pollOpenMessage = ""

        while move is None and failcnt < 3:

            if not lastMove is None:
                pollOpenMessage = "Player did {} === POLL OPEN === Write your move, poll closes in {} seconds".format(lastMove,
                self.vote_time)
            else:
                pollOpenMessage = " === POLL OPEN === Write your move, poll closes in {} seconds".format(
                self.vote_time)
            self.sendMessage(pollOpenMessage)

            chatDict[self.twitch_channel] = list()
            time.sleep(self.vote_time)

            move, cnt, wasRandom = self.getMoveFromChat(self.b)

            if move is None:
                self.sendMessage("No legal move was proposed, poll starts again.")
                failcnt += 1

        if failcnt >= 3:
            self.sendMessage("Poll was not successful multiple times, canceling game.")
            # cancel game
            try:
                requests.post("https://lichess.org/api/bot/game/{}/abort".format(self.gameid), headers=header)
            except:
                pass

            try:
                requests.post("https://lichess.org/api/bot/game/{}/resign".format(self.gameid), headers=header)
            except:
                pass

            return

        print(move, cnt)

        self.b.push(move)
        # write message to chat
        message = " === POLL CLOSED === Move {} won with {} votes.".format(move, cnt)

        if wasRandom:
            message = " === POLL CLOSED === Move {} won with {} votes (randomly chosen between same votes).".format(
                move, cnt)

        self.sendMessage(message)

        _r = requests.post("https://lichess.org/api/bot/game/{}/move/{}".format(self.gameid, move),
                           headers=header)

    def playGame(self):


        requests.post("https://lichess.org/api/challenge/{}/accept".format(self.gameid), headers=header)

        r = requests.get("https://lichess.org/api/bot/game/stream/{}".format(self.gameid), headers=header, stream=True)
        # https://lichess.org/api/bot/game/stream/{gameId}

        # create new chess board because we need to know
        # what moves are legal, so we don't spam lichess API
        self.b = chess.Board()

        amIwhite = False


        # read all events from game stream
        for line in r.iter_lines():

            # decode received JSON
            decoded_line = line.decode('utf-8')

            # ignore empty lines
            if (decoded_line != ''):

                j = json.loads(decoded_line)
                #print("XX ", j)

                # if game type finish, remove game from active game list
                # and go out of run function
                if j["type"] == "gameFinish":
                    _game = j["game"]

                    if _game["id"] == self.gameid:
                        activeGames.remove(self.twitch_channel)
                        print("GAME FINISHED AND ABORTED")
                        return

                # catch chat events in lichess, if not written by bot ( to avoid infinite loop )
                if j["type"] == "chatLine" and not self.configured and j["username"] != "tuxbot":

                    # parse text message
                    text = j["text"]

                    # if string consists of 2 words try to parse as twitch channel and timeout
                    if len(text.split(" ")) == 2:
                        self.twitch_channel, self.vote_time = text.split(" ")

                        self.twitch_channel = self.twitch_channel.lower()
                        
                        # lookup if lichess account is allowed to connect to mentioned twitch channel
                        if self.twitch_channel not in whitelist:
                            msg = "The mentioned twitch channel is not on the whitelist"
                            self.writeToLichessChat(msg)

                            msg = "Please contanct tuxmania to set your channel up for the first time"
                            self.writeToLichessChat(msg)

                            # cancel accepted challenge
                            Game.cancelResignGame(self.gameid)

                            # leave main game loop
                            return

                        # if channel is in whitelist but user not allowed to start the bot
                        if self.twitch_channel in whitelist and j["username"].lower() not in [x.lower() for x in
                                                                                         whitelist[self.twitch_channel]]:

                            msg = "You are not allowed to start the bot for this twitch channel."
                            self.writeToLichessChat(msg)

                            Game.cancelResignGame(self.gameid)

                            return

                        if self.twitch_channel in activeGames:
                            msg = "Game for channel {} is already running".format(self.twitch_channel)
                            self.writeToLichessChat(msg)

                            Game.cancelResignGame(self.gameid)

                            # close game call
                            return
                        else:
                            # if its legal to start bot, append channel to active games
                            # so it cannot be started twice
                            activeGames.append(self.twitch_channel)

                        print(self.twitch_channel, self.vote_time)
                        print(type(self.twitch_channel), type(self.vote_time))

                    # check if vote_time is between 10 and 60
                    try:
                        self.vote_time = int(self.vote_time)

                        if self.vote_time < 10 or self.vote_time > 60:
                            self.writeToLichessChat("Error: Vote time not in range 10 60")
                            continue
                    except:
                        self.writeToLichessChat("Error: vote time is not an integer")
                        continue

                    # set configuration flag to true
                    self.configured = True

                    self.getTwitchSocket()

                    #  start twitch chat reader async
                    twitch_thread = threading.Thread(target=self.startChatRead, args=(self.twitch_socket,self.twitch_channel))
                    twitch_thread.start()

                    msg = "Configuration finished"
                    self.writeToLichessChat(msg)

                    if amIwhite:

                        self.makeChatMove(None)

                if j["type"] == "gameFull":

                    print("!!", j)
                    print(j["white"]["id"])

                    if j["white"]["id"] == "tuxbot":
                        amIwhite = True

                    time.sleep(.5)

                    msg = "To set bot up, please reply with following message, vote time has to be between 10 and 60, twitch channel name is case sensitive"
                    msgjson = {"room": "player", "text": msg}
                    _r = requests.post("https://lichess.org/api/bot/game/{}/chat".format(self.gameid), headers=header,
                                       json=msgjson)
                    print(_r.json())

                    time.sleep(.5)

                    msg = "<TWITCH_CHANNELNAME> <vote time in seconds>"
                    msgjson = {"room": "player", "text": msg}
                    _r = requests.post("https://lichess.org/api/bot/game/{}/chat".format(self.gameid), headers=header,
                                       json=msgjson)
                    print(_r.json())

                if j["type"] == "gameState":

                    # check if resigned
                    status = j["status"]

                    if status in ["resign", "aborted", "mate", "draw"]:
                        try:
                            activeGames.remove(self.twitch_channel)
                        except:
                            pass

                        print("GAME FINISHED AND ABORTED")
                        return

                    moves = j["moves"]

                    if not self.configured:
                        time.sleep(.5)

                        msg = "To set bot up, please reply with following message, vote time has to be between 10 and 60, twitch channel name is case sensitive"

                        msgjson = {"room": "player", "text": msg}
                        _r = requests.post("https://lichess.org/api/bot/game/{}/chat".format(self.gameid), headers=header,
                                           json=msgjson)
                        print(_r.json())

                        time.sleep(.5)

                        msg = "<TWITCH_CHANNELNAME> <vote time in seconds>"
                        msgjson = {"room": "player", "text": msg}
                        _r = requests.post("https://lichess.org/api/bot/game/{}/chat".format(self.gameid), headers=header,
                                           json=msgjson)
                        print(_r.json())

                    # format move list from server
                    evenlist = len(moves.split(" ")) % 2 == 0

                    if (not evenlist and not amIwhite) or (evenlist and amIwhite):

                        # receive last move of player
                        lastmove = moves.split(" ")[-1]

                        # play last move on the board
                        self.b.push(chess.Move.from_uci(lastmove))

                        # chat interaction in order to determine next move:
                        self.makeChatMove(lastmove)


if __name__ == "__main__":

    config = configparser.ConfigParser()
    config.read("config.txt")

    token = config["DEFAULT"]["LichessToken"]
    twitch_token = config["DEFAULT"]["TwitchToken"]
    header = {"Authorization": "Bearer {}".format(token)}

    # get ongoing games
    r = requests.get("https://lichess.org/api/account/playing", headers=header)
    ongoingGames = r.json()["nowPlaying"]

    ongoingGameIds = list()

    for game in ongoingGames:
        ongoingGameIds.append(game["gameId"])

    for gid in ongoingGameIds:
        Game.cancelResignGame(gid)

    r = requests.get("https://lichess.org/api/stream/event", headers=header, stream=True)

    with open('whitelist.txt', 'r') as read_file:
        whitelist = json.load(read_file)


    print(token, twitch_token)

    for line in r.iter_lines():
        decoded_line = line.decode('utf-8')
        if(decoded_line != ''):

            j = json.loads(decoded_line)
            print(j["type"], decoded_line)

            if j["type"] == "challenge":
                _id = j["challenge"]["id"]

                t = threading.Thread(target=Game, args=(_id,))
                t.start()
