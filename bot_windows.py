import os
import discord
import sqlite3
import locale
import threading
import asyncio
import time

from datetime import datetime, timedelta
from discord.ext import commands
from dotenv import load_dotenv
from stream_check import *

# from .core import Group, Command

locale.setlocale(category=locale.LC_ALL,
                 locale="German")

# Database
conn = sqlite3.connect('NBombs.db', check_same_thread=False)
nbombCursor = conn.cursor()


# initially creates table
nbombCursor.execute('''CREATE TABLE IF NOT EXISTS nbombs(
    name text, time timestamp)''')

# loading environmental variables
load_dotenv()
TOKEN = os.getenv('DISCORD_TOKEN')
GUILD = os.getenv('DISCORD_GUILD')

# intents to get more permissions, in this cases to see all members, has to be enabled in the developer Portal aswell
intents = discord.Intents().all()

# initialising bot commands
bot = commands.Bot(command_prefix='!', intents=intents)
bot.remove_command("help")


# this function is called every hour and checks if its time to remove the nbomb


async def isItTime():
    while True:
        # timer which repeats this function every hour
        await asyncio.sleep(3600)
        # creating cursor
        checkEveryHourCursor = conn.cursor()
        # querying nbombs
        checkEveryHourCursor.execute('SELECT * FROM nbombs')
        rows = checkEveryHourCursor.fetchall()
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
        # print to see last check
        print("Last check was: " + now)

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
    # fetch all entries
    nbombCursor.execute("""
    SELECT *
    FROM nbombs
    """)
    rows = nbombCursor.fetchall()

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
    await ctx.send(embed=em)


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
