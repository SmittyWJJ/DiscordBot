import os
import discord
import sqlite3
import locale
import threading
import asyncio
import time
import twitch
import platform
import itertools

from dateutil import tz
from datetime import datetime, timedelta
from discord.ext import commands
from dotenv import load_dotenv
from stream_check import getSchedule

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
nbombCursor.execute("""CREATE TABLE IF NOT EXISTS floStreamSchedule(
    scheduledStartTime NUMERIC, scheduledEndTime NUMERIC, takenPlace NUMERIC, startedLate NUMERIC, endedEarly NUMERIC)""")


# loading environmental variables
load_dotenv()
TOKEN = os.getenv('DISCORD_TOKEN')
GUILD = os.getenv('DISCORD_GUILD_TEST')
GUILD_TEST = os.getenv("DISCORD_GUILD_TEST")
TWITCH_CLIENT_ID = os.getenv("TWITCH_CLIENT_ID")
TWITCH_CLIENT_SECRET = os.getenv("TWITCH_CLIENT_SECRET")
PRIMARY_CHANNEL = int(os.getenv("PRIMARY_CHANNEL"))
SECONDARY_CHANNEL = int(os.getenv("SECONDARY_CHANNEL"))

# intents to get more permissions, in this cases to see all members, has to be enabled in the developer Portal aswell
intents = discord.Intents().all()

# initialising bot commands
bot = commands.Bot(command_prefix='!', intents=intents)
bot.remove_command("help")

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


# checks if Rheyces stream is online 15 min after it was schedule to be online
# if its online takenPlace is set to 1 otherwise to 2


async def checkStreamLive():
    # set boolean to true as this function starts waiting
    global streamCheckStillRunning
    streamCheckStillRunning = True

    # new cursor and select all streams
    checkStreamCursor = conn.cursor()
    checkStreamCursor.execute("""
                        SELECT scheduledStartTime, scheduledEndTime, takenPlace
                        FROM floStreamSchedule
                        WHERE startedLate IS NULL
                        OR endedEarly IS NULL
                        """)
    rows = checkStreamCursor.fetchall()

    now = datetime.now()
    # iterate over all streams and look if any of them should be live now
    for stream in rows:
        # checking if we are less than 15 minutes away
        # then wait until we are 15min behind the supposed stream start and check if the stream is running
        if stream[0]:
            minutes_diff = (now - (datetime.strptime(
                stream[0], "%x - %H:%M:%S"))).total_seconds() / 60.0

            if minutes_diff < 15.0 and minutes_diff > 0.0:
                # wait until 15min after stream start
                await asyncio.sleep(int((15 - minutes_diff) * 60))
                # twitch api check if Rheyces live
                helix = twitch.Helix(TWITCH_CLIENT_ID, TWITCH_CLIENT_SECRET)
                if helix.user("Rheyces").is_live:
                    # set the tags according to what we know so far
                    checkStreamCursor.execute("""
                                                UPDATE floStreamSchedule
                                                SET takenPlace = 1, startedLate = 0
                                                WHERE scheduledStartTime = ?
                                                """, [stream[0]])
                else:
                    # set takenPlace to 2 if stream offline
                    checkStreamCursor.execute("""
                                                UPDATE floStreamSchedule
                                                SET takenPlace = 2, startedLate = 0
                                                WHERE scheduledStartTime = ?
                                                """, [stream[0]])
                break
        # checking if we are less than 30 and more than 15 minutes away from the stream end
        # then wait until exactly 15 min to check if stream is still running
        if stream[1]:
            minutes_diff = ((datetime.strptime(
                stream[0], "%x - %H:%M:%S")) - now).total_seconds() / 60.0

            if minutes_diff < 30.0 and minutes_diff > 15.0:
                # wait until 15min before stream end
                await asyncio.sleep(int((minutes_diff-15) * 60))
                # twitch api check if Rheyces live
                helix = twitch.Helix(TWITCH_CLIENT_ID, TWITCH_CLIENT_SECRET)
                if helix.user("Rheyces").is_live:
                    # set the tags according to the knowledge earned in the end
                    if stream[2] == 2:
                        checkStreamCursor.execute("""
                                                UPDATE floStreamSchedule
                                                SET takenPlace = 1, startedLate = 1, endedEarly = 0
                                                WHERE scheduledEndtTime = ?
                                                """, [stream[1]])
                    else:
                        checkStreamCursor.execute("""
                                                UPDATE floStreamSchedule
                                                SET takenPlace = 1, startedLate = 0, endedEarly = 0
                                                WHERE scheduledEndtTime = ?
                                                """, [stream[1]])
                else:
                    if stream[2] == 1:
                        checkStreamCursor.execute("""
                                                UPDATE floStreamSchedule
                                                SET endedEarly = 1
                                                WHERE scheduledEndtTime = ?
                                                """, [stream[1]])
                    else:
                        checkStreamCursor.execute("""
                                                UPDATE floStreamSchedule
                                                SET endedEarly = 0
                                                WHERE scheduledEndtTime = ?
                                                """, [stream[1]])
                break
    # close cursor and set boolean to false
    conn.commit()
    checkStreamCursor.close()
    streamCheckStillRunning = False

# checks the public google calendar for the next 10 upcoming events in this case Rheyces streams


async def checkSchedule():
    checkScheduleCursor = conn.cursor()
    startStreams = list()
    endStreams = list()
    events = getSchedule()

    # return if there are no scheduled events
    if not events:
        return
    # get every event start and end time
    for event in events:
        start = event['start'].get('dateTime', event['start'].get('date'))
        end = event['end'].get('dateTime', event['end'].get('date'))
        # convert both time formats
        scheduledStart = datetime.strptime(start, "%Y-%m-%dT%H:%M:%S%z")
        scheduledStartLocal = datetime.strftime(
            scheduledStart, "%x - %H:%M:%S")
        startStreams.append(scheduledStartLocal)
        scheduledEnd = datetime.strptime(end, "%Y-%m-%dT%H:%M:%S%z")
        scheduledEndLocal = datetime.strftime(
            scheduledEnd, "%x - %H:%M:%S")
        endStreams.append(scheduledEndLocal)

        # insert the stream if the exact same stream doesn't exist
        stringEntry = ""
        for (start, end) in zip(startStreams, endStreams):
            checkScheduleCursor.execute("""
                                INSERT INTO floStreamSchedule
                                (scheduledStartTime, scheduledEndTime, takenPlace)
                                    SELECT ?, ?, 0
                                    WHERE NOT EXISTS(
                                        SELECT *
                                        FROM floStreamSchedule
                                        WHERE scheduledStartTime = ?
                                        AND scheduledEndTime = ?
                                    )
                                """, (start, end, start, end))

    # commit
    conn.commit()

    checkScheduleCursor.close()


# checks if its time to remove the nbomb


async def checkNbombs():
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
        # check if nbombs need to be removed
        await checkNbombs()
        # check for new entry in Rheyces stream schedule
        await checkSchedule()
        # check if a stream is live, if the function is not already waiting
        # because this function starts running 15 min after stream start but gets called every 10 min
        if not streamCheckStillRunning:
            await checkStreamLive()
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


def checkIfNBombIsAlreadyAssigned(guild, nbomb):
    nbombCursor.execute('SELECT name FROM nbombs')
    rows = nbombCursor.fetchall()
    for element in rows:
        member = discord.utils.get(guild.members, name=element[0])
        if not member:
            if guild.name == GUILD_TEST:
                return
            deleteEntryFromDB(element[0])
            continue
        role = discord.utils.get(member.roles, name=nbomb.name)
        if role == None:
            deleteEntryFromDB(member.name)

# this is called when the programm is started


@bot.event
async def on_ready():
    # getting the guild for global use
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
        !flostats""")
    em.add_field(
        name="Syntax", value="""
        Zeigt eine Liste aller Commands.
        Weist der Person für [Tage] die N-Bombe zu.
        Zeigt eine Liste aller aktiven N-Bomben.
        Gibt Auskunft über die Zuverlässigkeit, was Flos Aussagen zu zukünftigen Streams angeht.
        """, inline=True)

    await ctx.send(embed=em)

# lists stats from Rheyces Stream


@bot.command(name='flostats', help='')
async def listStreamStats(ctx, *args):
    # channel check
    print(type(246235677983899663))
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

    # number of streams that have actually taken place
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

    # stats
    onlinePercentage = takenPlace / allAnnounced

    em = discord.Embed(
        title="Stream Stats", color=ctx.author.color)
    # RheycesPog
    em.set_thumbnail(
        url="https://static-cdn.jtvnw.net/emoticons/v1/302447893/3.0")
    em.add_field(name="Angekündigte Streams", value=str(allAnnounced))
    em.add_field(name="Stattgefundene Streams", value=str(takenPlace))
    em.add_field(name=f"Prozent", value=str(onlinePercentage) + "%")
    em.set_footer(text="Letzte Prüfung: {}".format(
        datetime.strftime(lastChecked, '%x - %H:%M:%S')))
    await ctx.send(embed=em)


# list all active nbombs


@bot.command(name='nbomben', help='Zeigt eine Liste aller N-Bomben an.')
async def listNbombs(ctx, *args):
    # channel check
    if ctx.channel.id != PRIMARY_CHANNEL and ctx.channel.id != SECONDARY_CHANNEL:
        return

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

    # check if correct amount of args were used
    if len(args) != 2:
        response = 'Benutze !nbombe [@Name] [Tage] um die N-Bombe zuzuweisen.'
        await ctx.send(response)
        return

    # getting guild and role object
    role = discord.utils.get(guild.roles, name="🆖💣")

    # check if roles on server represent entries in db, if someone on the server manually deleted the role than the data is not consistent
    checkIfNBombIsAlreadyAssigned(guild, role)

    # assign days to a variable
    daysToAssign = int(args[1])
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
        response = 'Die N-Bombe von {} wurde bis zum {} verlängert. <:x0r6ztGiggle4:785884713306816562>'.format(
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
