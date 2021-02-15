import os
import discord
import sqlite3
import locale
import threading
import asyncio
import time
import twitch

from dateutil import tz
from datetime import datetime, timedelta
from discord.ext import commands
from dotenv import load_dotenv
from stream_check import getSchedule

# from .core import Group, Command

# Windows: German
# Linux: de_DE.utf8
locale.setlocale(category=locale.LC_ALL,
                 locale="German")

# Database
conn = sqlite3.connect('NBombs.db', check_same_thread=False)
nbombCursor = conn.cursor()

# global variables
lastChecked = datetime.now()
streamCheckStillRunning = False

# initially creates table
nbombCursor.execute('''CREATE TABLE IF NOT EXISTS nbombs(
    name text, time timestamp)''')
nbombCursor.execute('''CREATE TABLE IF NOT EXISTS floStreamSchedule(
    scheduleTime NUMERIC, takenPlace NUMERIC)''')
nbombCursor.execute('''CREATE TABLE IF NOT EXISTS floStats(
    streamsAnnounced INTEGER, takenPlace INTEGER)''')

# loading environmental variables
load_dotenv()
TOKEN = os.getenv('DISCORD_TOKEN')
GUILD = os.getenv('DISCORD_GUILD')
TWITCH_CLIENT_ID = os.getenv("TWITCH_CLIENT_ID")
TWITCH_CLIENT_SECRET = os.getenv("TWITCH_CLIENT_SECRET")

# intents to get more permissions, in this cases to see all members, has to be enabled in the developer Portal aswell
intents = discord.Intents().all()

# initialising bot commands
bot = commands.Bot(command_prefix='!', intents=intents)
bot.remove_command("help")


# checks if Rheyces stream is online 15 min after it was schedule to be online
# if its online takenPlace is set to 1 otherwise to 2


async def checkStreamLive():
    # set boolean to true as this function starts waiting
    global streamCheckStillRunning
    streamCheckStillRunning = True

    # new cursor and select all streams
    checkStreamCursor = conn.cursor()
    checkStreamCursor.execute("""
                        SELECT *
                        FROM floStreamSchedule
                        WHERE takenPlace = 0
                        """)
    rows = checkStreamCursor.fetchall()

    now = datetime.now()
    # iterate over all streams and look if any of them should be live now
    for stream in rows:
        # checking if we are less than 15minutes away and then wait until we are 15min after stream start
        # should always work if we check every 10 min in isItTime() and 15 min in here

        # this would just check if we happen to be between 15 min and 30 min after stream start
        # minutes_diff = now - datetime.strptime(stream[0], "%x - %H:%M:%S")).total_seconds() / 60.0
        # if minutes_diff >= 15.0 and minutes_diff < 30.0:

        minutes_diff = (now - (datetime.strptime(
            stream[0], "%x - %H:%M:%S"))).total_seconds() / 60.0

        if minutes_diff < 15.0 and minutes_diff > 0.0:
            # wait until 15min after
            await asyncio.sleep(int((15 - minutes_diff) * 60))
            # twitch api check if Rheyces live
            helix = twitch.Helix(TWITCH_CLIENT_ID, TWITCH_CLIENT_SECRET)
            # this twitch query takes a good 2 seconds
            if helix.user("B0aty").is_live:
                # should be able to use the same cursor here if we break at the end anyway
                # set takenPlace to 1 if stream online
                checkStreamCursor.execute("""
                                            UPDATE floStreamSchedule
                                            SET takenPlace = 1
                                            WHERE scheduleTime = ?
                                            """, [stream[0]])
            else:
                # set takenPlace to 2 if stream offline so that we dont select an insane amount of streams if there are like 1000 cancelled streams
                checkStreamCursor.execute("""
                                            UPDATE floStreamSchedule
                                            SET takenPlace = 2
                                            WHERE scheduleTime = ?
                                            """, [stream[0]])
            break
    # close cursor and set boolean to false
    conn.commit()
    checkStreamCursor.close()
    streamCheckStillRunning = False

# checks the public google calendar for the next 10 upcoming events in this case Rheyces streams


async def checkSchedule():
    checkScheduleCursor = conn.cursor()
    streams = list()
    events = getSchedule()
    if not events:
        # no need to print, just spams the console
        # print('No upcoming events found.')
        return
    for event in events:
        start = event['start'].get('dateTime', event['start'].get('date'))
        scheduleTime = datetime.strptime(start, "%Y-%m-%dT%H:%M:%S%z")
        scheduleTimeLocal = datetime.strftime(scheduleTime, "%x - %H:%M:%S")
        streams.append(scheduleTimeLocal)

    # check if the stream is already in there otherwise it gets added over and over again
    checkScheduleCursor.execute("""
                        SELECT scheduleTime
                        FROM floStreamSchedule
                        """)
    rows = checkScheduleCursor.fetchall()

    for streamInRows in rows:
        for streamInList in streams:
            if streamInRows[0] == streamInList:
                streams.remove(streamInList)

    # if there are no new streams just return
    if not streams:
        return
    # a correct insert needs to look like this at the end
    # VALUES ("17.02.2021 - 17:00:00", 0), ...
    # while the last comma needs to be removed
    # stringEntry gets filled with all the streams and a 0 for not takenPlace yet
    stringEntry = ""
    for stream in streams:
        stringEntry += "(\"" + stream + "\", 0),"

    # queryString is prepared and the string entry gets added except the last symbol which is the comma
    # that needs to be removed
    # string[0:10] cuts the string
    queryString = """
                    INSERT INTO floStreamSchedule
                    (scheduleTime, takenPlace)
                    VALUES
                    """ \
                    + stringEntry[0:len(stringEntry)-1]

    # execute query
    checkScheduleCursor.execute(queryString)
    # commit
    conn.commit()

    checkScheduleCursor.close()

    # print(start, event['summary'])


# checks if its time to remove the nbomb


async def checkNbombs():
    # creating cursor
    checkNBombCursor = conn.cursor()
    # querying nbombs
    checkNBombCursor.execute('SELECT * FROM nbombs')
    rows = checkNBombCursor.fetchall()
    # getting time and guild
    now = datetime.now()
    guild = discord.utils.get(bot.guilds, name=GUILD)
    role = discord.utils.get(guild.roles, name="🆖💣")
    for a in rows:
        if datetime.strptime(a[1], "%x - %H:%M:%S") < now:
            member = discord.utils.get(guild.members, name=a[0])
            await member.remove_roles(role)
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
    # inserts an entry if that name does'nt exists yet
    nbombCursor.execute('''
                                      INSERT INTO nbombs
                                      (name, time)
                                        SELECT
                                        ?,
                                        ?
                                        WHERE NOT EXISTS(
                                            SELECT name
                                            FROM nbombs
                                            WHERE name = ?
                                        )
                  ''', (member, time, member))

# update the db by days given


def updateDB(time, member):
    nbombCursor.execute('''
                        UPDATE nbombs
                        SET time = ?
                        WHERE name = ?
                        ''', (time, member))
# check if the roles on the server and the entries on the db are consistent

# delete entry from db


def deleteEntryFromDB(member):
    nbombCursor.execute('''
                        DELETE FROM nbombs
                        WHERE name = ?
    ''', [member])

# not sure how costly this is to performance could be left out


def checkIfNBombIsAlreadyAssigned(guild, nbomb):
    nbombCursor.execute('SELECT name FROM nbombs')
    rows = nbombCursor.fetchall()
    for element in rows:
        member = discord.utils.get(guild.members, name=element[0])
        if not member:
            if guild.name == "Server von Dschanner":
                return
            deleteEntryFromDB(element[0])
            continue
        role = discord.utils.get(member.roles, name=nbomb.name)
        if role == None:
            deleteEntryFromDB(member.name)

# this is called when the programm is started


@bot.event
async def on_ready():
    await isItTime()

# help command


@bot.group(invoke_without_command=True)
async def help(ctx):
    # create and fill embedded message
    em = discord.Embed(
        title="Commands", color=ctx.author.color)

    em.add_field(name="Commands", value="""
        !help
        !nbombe [@Person] [Tage]
        !nbomben""")
    em.add_field(
        name="Syntax", value="""
        Zeigt eine Liste aller Commands.
        Weist der Person für [Tage] die N-Bombe zu.
        Zeigt eine Liste aller aktiven N-Bomben.
        """, inline=True)

    await ctx.send(embed=em)

# list all active nbombs


@bot.command(name='nbomben', help='Zeigt eine Liste aller NBomben an.')
async def listNbombs(ctx, *args):
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
                nbombs.append([row[0], row[1], timeLeft.days])
            nbombs.sort(key=lambda x: x[2])

    # fill the field strings to post them afterwards
    name, date, days = "", "", ""
    for person in nbombs:
        name += person[0]+"\n"
        date += person[1]+"\n"
        days += str(person[2])+"\n"
    em = discord.Embed(
        title="N-Bomben", color=ctx.author.color)
    em.set_thumbnail(
        url="https://cdn160.picsart.com/upscale-253053797003212.png")
    em.add_field(name="Name", value=name)
    em.add_field(name="Übrige Tage", value=days)
    em.add_field(name=f"Genauer Zeitpunkt", value=date)
    em.set_footer(text="Letzte Prüfung: {}".format(
        datetime.strftime(lastChecked, '%x - %H:%M:%S')))
    await ctx.send(embed=em)
    listNbombCursor.close()


# assign nbomb


@bot.command(name='nbombe', help='[Name] [Tage] weist dieser Person [Tage] die NBombe zu.')
async def giveNbombRole(ctx, *args):
    # check if correct amount of args were used
    if len(args) != 2:
        response = 'Benutze !nbombe [@Name] [Tage] um die N-Bombe zuzuweisen.'
        await ctx.send(response)
        return

    # getting guild and role object
    guild = ctx.guild
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
    nbombCursor.execute('''
           SELECT *
           FROM nbombs
           WHERE name = ?
       ''', [userToAssignNbomb.name])
    rows = nbombCursor.fetchall()

    # if nothing was found then make new entry
    # else increase time by daysToAssign days
    if not rows:
        # insert
        insertIntoDB(timeToAssign, userToAssignNbomb.name)

        # assign role to user by calling add_roles on the user object
        await userToAssignNbomb.add_roles(role)

        # sending response to assigning role
        response = '{} wurde bis zum {} die NBombe zugewiesen.'.format(
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
        response = 'Die NBombe von {} wurde bis zum {} verlängert <:x0r6ztGiggle4:785884713306816562>'.format(
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
