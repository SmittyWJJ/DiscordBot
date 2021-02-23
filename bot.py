import asyncio
import discord
import itertools
import locale
import logging
import os
import platform
import sqlite3
import stream_check
import threading
import time
import twitch

from datetime import datetime, timedelta
from dateutil import tz
from discord.ext import commands
from dotenv import load_dotenv

# from .core import Group, Command


# Database
conn = sqlite3.connect('NBombs.db', check_same_thread=False)
nbombCursor = conn.cursor()

# initially creates table
# name                  -   Name of the user
# time                  -   Time string
nbombCursor.execute("""CREATE TABLE IF NOT EXISTS nbombs(
    name text, time timestamp)""")
# scheduledStartTime    -   Time where the stream was supposed to start
# scheduledEndTime      -   Time where the stream was supposed to end
# takenPlace            -   Was the stream live 15 min after the start or 15 min before the end?
#                               0 -> announced
#                               1 -> taken place 15 min after start time
#                               2 -> has not taken place 15 min after start time
# startedLate           -   Was the stream offline 15 min after the start but online 15 min before the end?
#                       -       0 -> no
#                       -       1 -> yes
# endedEarly            -   Was the stream online 15 min after the start but offline 15 min before the end?
#                       -       0 -> no
#                       -       1 -> yes
# duration              -   How long is the stream?
# streamTopic           -   The description of the calendar entry
nbombCursor.execute("""CREATE TABLE IF NOT EXISTS floStreamSchedule(
    scheduledStartTime NUMERIC, scheduledEndTime NUMERIC, takenPlace NUMERIC, startedLate NUMERIC, endedEarly NUMERIC, duration NUMERIC, streamTopic TEXT)""")


# loading environmental variables
load_dotenv()
TOKEN = os.getenv('DISCORD_TOKEN')
GUILD = os.getenv('DISCORD_GUILD')
GUILD_TEST = os.getenv("DISCORD_GUILD_TEST")
# TWITCH_CLIENT_ID = os.getenv("TWITCH_CLIENT_ID")
# TWITCH_CLIENT_SECRET = os.getenv("TWITCH_CLIENT_SECRET")
PRIMARY_CHANNEL = int(os.getenv("PRIMARY_CHANNEL"))
SECONDARY_CHANNEL = int(os.getenv("SECONDARY_CHANNEL"))

# intents to get more permissions, in this cases to see all members, has to be enabled in the developer Portal aswell
intents = discord.Intents().all()

# initialising bot commands
bot = commands.Bot(command_prefix='!', intents=intents)
bot.remove_command("help")

# initialise logger
LOG_FILENAME = "log.log"
mylogger = logging.getLogger("mylogger")
streamHandler = logging.StreamHandler()
fileHandler = logging.FileHandler(LOG_FILENAME)
mylogger.addHandler(streamHandler)
mylogger.addHandler(fileHandler)
mylogger.setLevel(logging.INFO)

# global variables
lastChecked = datetime.now()
streamCheckStillRunning = False
guild = None
systemOS = platform.system()

# probably breaks if neither windows nor linux is used but at that point the whole bot is probably dead
if systemOS == "Windows":
    locale.setlocale(category=locale.LC_ALL,
                     locale="German")
else:
    locale.setlocale(category=locale.LC_ALL,
                     locale="de_DE.utf8")


# checks if its time to remove the nbomb


async def checkNbombs():

    # check if roles on server represent entries in db, if someone on the server manually deleted the role than the data is not consistent
    # and the role gets deleted from db
    checkIfNBombIsAlreadyAssigned()

    # creating cursor
    checkNBombCursor = conn.cursor()
    # querying nbombs
    checkNBombCursor.execute('SELECT * FROM nbombs')
    rows = checkNBombCursor.fetchall()
    # getting time
    now = datetime.now()
    role = discord.utils.get(guild.roles, name="🆖💣")
    for a in rows:
        if datetime.strptime(a[1], "%x - %H:%M:%S") < now:
            member = discord.utils.get(guild.members, name=a[0])
            await member.remove_roles(role)
            # send message in #kuschelkrabbe
            channel = discord.utils.get(guild.channels, id=PRIMARY_CHANNEL)
            await channel.send(
                content="<@!{}> wurde die N-Bombe abgenommen. <:FeelsOkayMan:811338559394676758>".format(member.id))
            deleteEntryFromDB(member.name)
    conn.commit()
    checkNBombCursor.close()
    # print to see last check
    global lastChecked
    lastChecked = now
    print("Last check was: " + datetime.strftime(now, '%x - %H:%M:%S'))

# this function is called every 10mins


async def isItTime():
    while True:
        global streamCheckStillRunning
        # check if nbombs need to be removed
        await checkNbombs()
        # check for new entry in Rheyces stream schedule
        await stream_check.checkSchedule()
        # check if a stream is live, if the function is not already waiting
        # because this function starts running 15 min after stream start but gets called every 10 min
        if not streamCheckStillRunning:
            streamCheckStillRunning = True
            await stream_check.checkStreamLive()
            streamCheckStillRunning = False
        # timer which repeats this function every 10 mins
        await asyncio.sleep(600)

# function to insert one entry to track


def insertIntoDB(time, member):
    # inserts an entry if that name doesn't exists yet
    nbombCursor.execute("""
                                      INSERT INTO nbombs
                                      (name, time)
                                        SELECT
                                        ?, ?
                                        WHERE NOT EXISTS(
                                            SELECT name
                                            FROM nbombs
                                            WHERE name = ?
                                        )
                  """, (member, time, member))

# update the db by days given


def updateDB(time, member):
    nbombCursor.execute("""
                        UPDATE nbombs
                        SET time = ?
                        WHERE name = ?
                        """, (time, member))
# check if the roles on the server and the entries on the db are consistent

# delete entry from db


def deleteEntryFromDB(member):
    nbombCursor.execute("""
                        DELETE FROM nbombs
                        WHERE name = ?
    """, [member])

# not sure how costly this is to performance could be left out


def checkIfNBombIsAlreadyAssigned():
    nbombCursor.execute('SELECT name FROM nbombs')
    rows = nbombCursor.fetchall()
    for element in rows:
        member = discord.utils.get(guild.members, name=element[0])
        if not member:
            if guild.name == GUILD_TEST:
                return
            deleteEntryFromDB(element[0])
            continue
        role = discord.utils.get(member.roles, name="🆖💣")
        if role == None:
            deleteEntryFromDB(member.name)

# this is called when the programm is started


@bot.event
async def on_ready():
    # getting the guild for global use

    # preparation for twitch listener
    # print(stream_check.makeTwitchApiRequest("https://api.twitch.tv/helix/users?login=rheyces"))

    global guild
    guild = discord.utils.get(bot.guilds, name=GUILD)
    await isItTime()

# help command


@bot.group(invoke_without_command=True)
async def help(ctx):
    # channel check
    if ctx.channel.id != PRIMARY_CHANNEL and ctx.channel.id != SECONDARY_CHANNEL:
        return

    # create and fill embedded message
    em = discord.Embed(color=ctx.author.color)

    em.add_field(name="Commands", value="""
        !help
        !nbombe [@Person] [Tage]
        !nbomben
        !flostats
        !streams""")
    em.add_field(
        name="Syntax", value="""
        Zeigt eine Liste aller Commands.
        Weist der Person für [Tage] die N-Bombe zu.
        Zeigt eine Liste aller aktiven N-Bomben.
        Gibt Auskunft über die Zuverlässigkeit, was Flos Aussagen zu zukünftigen Streams angeht.
        Zeigt die kommenden Streams an.
        """, inline=True)

    await ctx.send(embed=em)


# # list all active nbombs


# @bot.command(name='streams', help='')
# async def listStreams(ctx, *args):
#     # channel check
#     if ctx.channel.id != PRIMARY_CHANNEL and ctx.channel.id != SECONDARY_CHANNEL:
#         return

#     # log who wrote it when
#     now = datetime.now()
#     mylogger.info(str(now) + " - " + ctx.message.author.name +
#                   ": " + ctx.message.content)

#     streams = stream_check.getSchedule()


# lists stats from Rheyces Stream


@bot.command(name='flostats', help='')
async def listStreamStats(ctx, *args):
    # channel check
    if ctx.channel.id != PRIMARY_CHANNEL and ctx.channel.id != SECONDARY_CHANNEL:
        return
    # new cursor and select stats
    listStreamStatsCursor = conn.cursor()
    # number of streams that have ever been announced
    listStreamStatsCursor.execute("""
                                    SELECT count(*)
                                    FROM floStreamSchedule
                                    """)
    allAnnounced = listStreamStatsCursor.fetchall()[0][0]

    # number of streams that have takenPlace or were cancelled
    listStreamStatsCursor.execute("""
                                    SELECT count(*)
                                    FROM floStreamSchedule
                                    WHERE NOT takenPlace = 0
                                    """)
    takenPlaceOrCancelled = listStreamStatsCursor.fetchall()[0][0]

    # number of streams that have takenPlace
    listStreamStatsCursor.execute("""
                                    SELECT count(*)
                                    FROM floStreamSchedule
                                    WHERE takenPlace = 1
                                    """)
    takenPlace = listStreamStatsCursor.fetchall()[0][0]

    # number of streams that have been canceled
    listStreamStatsCursor.execute("""
                                    SELECT count(*)
                                    FROM floStreamSchedule
                                    WHERE takenPlace = 2
                                    """)
    cancelled = listStreamStatsCursor.fetchall()[0][0]

    # number of streams that have started late
    listStreamStatsCursor.execute("""
                                    SELECT count(*)
                                    FROM floStreamSchedule
                                    WHERE startedLate = 1
                                    """)
    startedLate = listStreamStatsCursor.fetchall()[0][0]

    # number of streams that have ended early
    listStreamStatsCursor.execute("""
                                    SELECT count(*)
                                    FROM floStreamSchedule
                                    WHERE endedEarly = 1
                                    """)
    endedEarly = listStreamStatsCursor.fetchall()[0][0]

    # date of the last stream thats taken place
    listStreamStatsCursor.execute("""
                                    SELECT *
                                    FROM floStreamSchedule
                                    WHERE takenPlace = 1
                                    """)
    lastStreamTakenPlace = listStreamStatsCursor.fetchall()
    lastStreamIndex = len(lastStreamTakenPlace)-1
    if lastStreamTakenPlace:
        lastStreamTakenPlaceDate = (
            lastStreamTakenPlace[lastStreamIndex][0])[0:10]
        lastStreamTakenPlaceHour = (
            lastStreamTakenPlace[lastStreamIndex][0])[13:18]
        lastStreamTakenPlaceStartedLate = lastStreamTakenPlace[lastStreamIndex][3]
        lastStreamTakenPlaceEndedEarly = lastStreamTakenPlace[lastStreamIndex][4]
        lastStreamTakenPlaceDuration = (
            lastStreamTakenPlace[lastStreamIndex][5]).split(":")

    # date of the last stream that was cancelled
    listStreamStatsCursor.execute("""
                                    SELECT *
                                    FROM floStreamSchedule
                                    WHERE takenPlace = 0
                                    AND startedLate = 0
                                    AND endedEarly = 0
                                    """)
    lastStreamCancelled = listStreamStatsCursor.fetchall()
    if lastStreamCancelled:
        lastStreamCancelledDate = (lastStreamCancelled[0][0])[0:10]
        lastStreamCancelledStartHour = (lastStreamCancelled[0][0])[13:18]
        lastStreamCancelledEndHour = (lastStreamCancelled[0][1])[13:18]

    # date of the next stream
    listStreamStatsCursor.execute("""
                                    SELECT *
                                    FROM floStreamSchedule
                                    WHERE takenPlace = 0
                                    AND startedLate IS NULL
                                    AND endedEarly IS NULL
                                    """)
    nextStream = listStreamStatsCursor.fetchall()
    if nextStream:
        nextStreamDate = (nextStream[0][0])[0:10]
        nextStreamStartHour = (nextStream[0][0])[13:18]
        nextStreamEndHour = (nextStream[0][1])[13:18]
        nextStreamTopic = nextStream[0][6]

    # stats
    if takenPlaceOrCancelled == 0:
        onlinePercentage = 0
    else:
        onlinePercentage = round((takenPlace / takenPlaceOrCancelled) * 100)

    em = discord.Embed(
        title="Stream Stats", color=ctx.author.color)
    # RheycesPog
    em.set_thumbnail(
        url="https://static-cdn.jtvnw.net/emoticons/v1/302447893/3.0")
    em.add_field(name="Angekündigt", value=str(allAnnounced))
    em.add_field(name="Stattgefunden", value=str(takenPlace))
    em.add_field(name="Ausgefallen", value=str(cancelled))
    em.add_field(name="Zu spät gestartet", value=str(startedLate))
    em.add_field(name="Zu früh beendet", value=str(endedEarly))
    em.add_field(name="Zuverlässigkeit",
                 value=str(onlinePercentage) + "%")
    # descision tree for what to write for the last stream
    # there is no last stream
    if not lastStreamTakenPlace:
        em.add_field(name="Letzter Stream", inline=False,
                     value="Es gab noch keinen Stream.")
    # last stream started late
    elif lastStreamTakenPlaceStartedLate == 1:
        em.add_field(name="Letzter Stream", inline=False,
                     value="Der letzte Stream war am **{}** um **{}** Uhr und sollte **{}:{}** Stunden gehen. Jedoch wurde der Stream zu spät gestartet. <:weirdChamp:754793653318320298>".format(lastStreamTakenPlaceDate, lastStreamTakenPlaceHour, lastStreamTakenPlaceDuration[0], lastStreamTakenPlaceDuration[1]))
    # last stream ended early
    elif lastStreamTakenPlaceEndedEarly == 1:
        em.add_field(name="Letzter Stream", inline=False,
                     value="Der letzte Stream war am **{}** um **{}** Uhr und sollte **{}:{}** Stunden gehen. Jedoch wurde der Stream zu früh beendet. <:weirdChamp:754793653318320298>".format(lastStreamTakenPlaceDate, lastStreamTakenPlaceHour, lastStreamTakenPlaceDuration[0], lastStreamTakenPlaceDuration[1]))
    # last stream had no issues
    else:
        em.add_field(name="Letzter Stream", inline=False,
                     value="Der letzte Stream war am **{}** um **{}** Uhr und ging **{}:{}** Stunden.".format(lastStreamTakenPlaceDate, lastStreamTakenPlaceHour, lastStreamTakenPlaceDuration[0], lastStreamTakenPlaceDuration[1]))

    # descision tree for what to write for the last cancelled stream
    # no stream cancelled yet
    if not lastStreamCancelled:
        em.add_field(name="Letzter ausgefallener Stream", inline=False,
                     value="Es ist noch kein Stream ausgefallen. <:FeelsGoodMan:327518256254418954>")
    # last stream cancelled
    else:
        em.add_field(name="Letzter ausgefallener Stream", inline=False,
                     value="Der letzte Stream sollte am **{}** von **{}** Uhr bis **{}** Uhr stattfinden. Leider war das nicht der Fall. <:FeelsWeird:754793366440640582>".format(lastStreamCancelledDate, lastStreamCancelledStartHour, lastStreamCancelledEndHour))

    # descision tree for what to write for the next stream
    if not nextStream:
        em.add_field(name="Nächster Stream", inline=False,
                     value="Es ist noch kein nächster Stream angekündigt. <:FeelsBadMan:327518231105503243>")
    else:
        timeUntilNextStream = datetime.strptime(
            nextStream[0][0], '%x - %H:%M:%S') - datetime.now()
        if timeUntilNextStream.days < 0:
            em.add_field(name="Nächster Stream", inline=False,
                         value="Es ist noch kein nächster Stream angekündigt. <:FeelsBadMan:327518231105503243>")
        else:
            hoursLeft = timeUntilNextStream.seconds/3600
            nextStreamFieldString = "Der nächste Steam ist am **{}** von **{}** Uhr bis **{}** Uhr geplant. Also noch {} Tage, {} Stunden und {} Minuten warten.".format(
                nextStreamDate, nextStreamStartHour, nextStreamEndHour, str(timeUntilNextStream.days), int(hoursLeft),  int((hoursLeft-int(hoursLeft))*60))
            if nextStreamTopic:
                nextStreamFieldString += f"\n\nGestreamt wird: **{nextStreamTopic}**"
            em.add_field(name="Nächster Stream", inline=False,
                         value=nextStreamFieldString)

    em.set_footer(text="Letzte Prüfung: {}".format(
        datetime.strftime(lastChecked, '%x - %H:%M:%S')))
    await ctx.send(embed=em)


# list all active nbombs


@bot.command(name='nbomben', help='Zeigt eine Liste aller N-Bomben an.')
async def listNbombs(ctx, *args):
    # channel check
    if ctx.channel.id != PRIMARY_CHANNEL and ctx.channel.id != SECONDARY_CHANNEL:
        return

    # log who wrote it when
    now = datetime.now()
    mylogger.info(str(now) + " - " + ctx.message.author.name +
                  ": " + ctx.message.content)

    listNbombCursor = conn.cursor()
    # main()
    # fetch all entries
    listNbombCursor.execute("""
    SELECT *
    FROM nbombs
    """)
    rows = listNbombCursor.fetchall()

    # check if anyone has the nbomb
    if not rows:
        response = "Momentan hat niemand die N-Bombe."
        await ctx.send(response)
        return
    else:
        nbombs = list()
        # sort the list by time left if there are more than one
        if len(rows) > 1:
            for row in rows:
                nBombUntil = datetime.strptime(row[1], '%x - %H:%M:%S')
                now = datetime.now()
                timeLeft = nBombUntil - now
                hoursLeft = timeLeft.seconds/3600
                nbombs.append(
                    [row[0], row[1], timeLeft.days, int(hoursLeft), int((hoursLeft-int(hoursLeft))*60)])
            nbombs.sort(key=lambda person: (person[2], person[0]))

    # fill the field strings to post them afterwards
    name, date, time = "", "", ""
    for person in nbombs:
        name += person[0]+"\n"
        date += person[1]+"\n"
        if person[2] != 0:
            time += str(person[2]) + "d " + str(person[3]
                                                ) + "h " + str(person[4]) + "min\n"
        else:
            time += str(person[3]) + "h " + str(person[4]) + "min\n"
    em = discord.Embed(
        title="N-Bomben", color=ctx.author.color)
    em.set_thumbnail(
        url="https://cdn160.picsart.com/upscale-253053797003212.png")
    em.add_field(name="Name", value=name)
    em.add_field(name="Übrige Zeit", value=time)
    em.add_field(name=f"Genauer Zeitpunkt", value=date)
    em.set_footer(text="Letzte Prüfung: {}".format(
        datetime.strftime(lastChecked, '%x - %H:%M:%S')))
    await ctx.send(embed=em)
    listNbombCursor.close()


# assign nbomb


@bot.command(name='nbombe', help='[Name] [Tage] weist dieser Person [Tage] die N-Bombe zu.')
async def giveNbombRole(ctx, *args):
    # channel check
    if ctx.channel.id != PRIMARY_CHANNEL and ctx.channel.id != SECONDARY_CHANNEL:
        return

    # log who wrote it when
    now = datetime.now()
    mylogger.info(str(now) + " - " + ctx.message.author.name +
                  ": " + ctx.message.content)

    # check if correct amount of args were used
    if len(args) != 2:
        response = 'Benutze !nbombe [@Name] [Tage] um die N-Bombe zuzuweisen.'
        await ctx.send(response)
        return

    # getting guild and role object
    role = discord.utils.get(guild.roles, name="🆖💣")

    # assign days to a variable
    daysToAssign = int(args[1])

    if daysToAssign == 0:
        response = '0 ist keine valide Zeit.'
        await ctx.send(response)
        return

    # stripping symbols of @Role
    userToAssignNbombId = args[0].strip("<@!>")

    # getting user object from the id
    userToAssignNbomb = discord.utils.get(
        guild.members, id=int(userToAssignNbombId))

    # calculating time
    now = datetime.now()
    now += timedelta(days=daysToAssign)
    timeToAssign = now.strftime("%x - %H:%M:%S")

    # check if name is already in db and save to cursor
    nbombCursor.execute("""
           SELECT *
           FROM nbombs
           WHERE name = ?
       """, [userToAssignNbomb.name])
    rows = nbombCursor.fetchall()

    # return if we are on test server
    if guild.name == GUILD_TEST:
        return

    # if nothing was found then make new entry
    # else increase time by daysToAssign days
    if not rows:
        # insert
        insertIntoDB(timeToAssign, userToAssignNbomb.name)

        # assign role to user by calling add_roles on the user object
        await userToAssignNbomb.add_roles(role)

        # sending response to assigning role
        response = '{} wurde bis zum {} die N-Bombe zugewiesen.'.format(
            args[0], timeToAssign)
        await ctx.send(response)
    else:
        # convert to datetime to increase time by 7 days
        time = datetime.strptime(rows[0][1], '%x - %H:%M:%S')
        time += timedelta(days=daysToAssign)
        # convert back to string to save in db later
        time = datetime.strftime(time, '%x - %H:%M:%S')
        updateDB(time, userToAssignNbomb.name)
        # sending response to assigning role
        if daysToAssign > 0:
            response = 'Die N-Bombe von {} wurde bis zum {} verlängert. <:x0r6ztGiggle4:785884713306816562>'.format(
                args[0], time)
        else:
            response = 'Die N-Bombe von {} wurde bis zum {} verkürzt. <:EZ:754793307418132491>'.format(
                args[0], time)
        await ctx.send(response)

    # for rows in nbombCursor.execute('SELECT * FROM nbombs'):
    # print(rows)

    conn.commit()


# list all single help commands


# @help.command()
# async def kick(ctx):
  #  em = discord.Embed(title="NBombe", description="Kicks a ",
 #                      color=ctx.author.color)
#
    # signature = bot.get_command_signature('nbombe')
    # for x in bot.commands:
    #   print(x.help)
   # text = ""
  #  for x in bot.commands:
 #       text += str(x.help)
#
  #  em.add_field(name="**Syntax**",
 #                value=text)
#
#    await ctx.send(embed=em)

bot.run(TOKEN)

conn.close()
