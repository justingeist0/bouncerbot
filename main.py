import discord
from discord.ext import commands
import sqlite3
from PIL import Image
import requests
from io import BytesIO
import imagehash

db_file_name = 'admins.db'


def create_table_and_db_if_does_not_exist():
    try:
        # Connect to the SQLite database. If it doesn't exist, it will be created.
        connection = sqlite3.connect(db_file_name)
        # Create a cursor object to execute SQL commands
        cursor = connection.cursor()
        # Create a table
        cursor.execute('''CREATE TABLE IF NOT EXISTS users
                          (id INTEGER PRIMARY KEY,
                          guild_id INTEGER,
                          user_id INTEGER)''')
        connection.close()
    except Exception as e:
        print(f"Error creating the database: {str(e)}")
        return False


create_table_and_db_if_does_not_exist()

intents = discord.Intents.all()
intents.members = True  # Enable member updates.
bot = commands.Bot(command_prefix='!', intents=intents)


@bot.event
async def on_ready():
    print(f'Bot is ready as {bot.user.name}')


@bot.event
async def on_member_join(member):
    await kick_if_scammer(member.guild.id, member.id)


@bot.event
async def on_member_update(before, after):
    await kick_if_scammer(after.guild.id, after.id)


@bot.command(name="bounceradd")
async def bouncer_add(ctx, target: str = None):
    if not is_admin(ctx):
        return
    if target and len(ctx.message.mentions) > 0:
        user = ctx.message.mentions[0]
    else:
        user = ctx.message.author
    user_id = user.id
    guild_id = ctx.guild.id
    if exists(guild_id, user_id):
        success = delete_user_guild_entry(guild_id, user_id)
        if success:
            await ctx.send(f"No longer blocking impersonators for <@!{user_id}>")
        else:
            await ctx.send(f"Error, sorry about that idk what's up.")
    else:
        success = add_user_guild_entry(guild_id, user_id)
        if success:
            await ctx.send(f"Now blocking impersonators for <@!{user_id}>")
        else:
            await ctx.send(f"Error, sorry about that idk what's up.")


@bot.command(name="bouncerlist")
async def list_blockers(ctx):
    if not is_admin(ctx):
        return
    user_ids = get_user_ids_by_guild_id(ctx.guild.id)
    user_ids = [f"<@!{i}>" for i in user_ids]
    await ctx.send(str(user_ids))


@bot.command(name="bouncer")
async def bouncer_list_commands(ctx):
    if not is_admin(ctx):
        return
    await ctx.send("Bouncer commands:\n"
                   "!bouncer - List Commands\n"
                   "!bouncerlist - List all the people we block impersonation.\n"
                   "!bounderadd @NAME - Add or remove someone to the admin list. "
                   "\nThe bouncer will kick people who match the same display name & profile pic.\n\n"
                   "For support hit up @FantasmaDev on X.")


def is_admin(ctx):
    return any(role.permissions.administrator for role in ctx.author.roles)


async def kick_if_scammer(guild_id, user_id):
    users = get_user_ids_by_guild_id(guild_id)
    if user_id in users:
        return
    guild = await bot.fetch_guild(guild_id)
    user = await guild.fetch_member(user_id)
    discord_user = await bot.fetch_user(user_id)
    for u in users:
        admin_user = await guild.fetch_member(u)
        admin_name = admin_user.nick if admin_user.nick is not None else admin_user.name
        admin_name = str(admin_name).lower()
        # If nickname not equal we are good to continue
        if discord_user.global_name.lower() != admin_name and user.nick != admin_name:
            continue
        # If avatars are at least 80% similar kick the imposter!
        admin_avatar = admin_user.avatar
        user_avatar = user.avatar
        if image_similarity(admin_avatar, user_avatar) > .8:
            await user.kick()
            await admin_user.send(f"Just kicked someone who was trying to impersonate you <@!{user_id}> in your server")
            await user.send(f"You were kicked from a server because your profile pic and name matched the admins. Stop scamming people you heartless poopy head.")
            break


def get_user_ids_by_guild_id(guild_id):
    try:
        # Connect to the SQLite database
        connection = sqlite3.connect(db_file_name)
        cursor = connection.cursor()

        cursor.execute("SELECT user_id FROM users WHERE guild_id=?", (guild_id,))
        user_ids = cursor.fetchall()

        connection.close()

        # Extract the user_ids from the result
        user_ids = [user_id[0] for user_id in user_ids]

        return user_ids
    except Exception as e:
        print(f"Error retrieving user IDs: {str(e)}")
        return []


def delete_user_guild_entry(guild_id, user_id):
    try:
        # Connect to the SQLite database
        connection = sqlite3.connect(db_file_name)
        cursor = connection.cursor()

        # Execute a DELETE query to remove the entry with the specified guild_id and user_id
        cursor.execute("DELETE FROM users WHERE guild_id=? AND user_id=?", (guild_id, user_id))

        # Commit the changes to the database
        connection.commit()
        connection.close()
        return True  # Deletion successful
    except Exception as e:
        print(f"Error deleting entry: {str(e)}")
        return False  # Deletion failed


def exists(guild_id, user_id):
    try:
        # Connect to the SQLite database
        connection = sqlite3.connect(db_file_name)
        cursor = connection.cursor()

        cursor.execute("SELECT 1 FROM users WHERE guild_id=? AND user_id=?", (guild_id, user_id))
        existing_entry = cursor.fetchone()
        connection.close()
        if not existing_entry:
            return False

        return True  # Add successful
    except Exception as e:
        print(f"Error adding entry: {str(e)}")
        return False


def add_user_guild_entry(guild_id, user_id):
    try:
        # Connect to the SQLite database
        connection = sqlite3.connect(db_file_name)
        cursor = connection.cursor()

        cursor.execute("INSERT OR IGNORE INTO users (guild_id, user_id) VALUES (?, ?)", (guild_id, user_id))

        # Commit the changes to the database
        connection.commit()

        connection.close()

        return True  # Add successful
    except Exception as e:
        print(f"Error adding entry: {str(e)}")
        return False  # Deletion failed


def image_similarity(url1, url2):
    try:
        # Download the images from the URLs
        response1 = requests.get(url1)
        response2 = requests.get(url2)

        # Check if the responses are successful
        if response1.status_code != 200 or response2.status_code != 200:
            return 0  # Return 0 for unsuccessful downloads

        # Open the downloaded images with PIL
        image1 = Image.open(BytesIO(response1.content))
        image2 = Image.open(BytesIO(response2.content))

        # Calculate the perceptual hash (pHash) of the images
        hash1 = imagehash.average_hash(image1)
        hash2 = imagehash.average_hash(image2)

        # Calculate the hamming distance between the hashes
        hamming_distance = hash1 - hash2

        # Calculate the similarity as a percentage
        similarity = 1.0 - (hamming_distance / 64.0)  # 64 is the max hamming distance for an average hash
        print(similarity)
        return similarity
    except Exception as e:
        print(f"Error: {str(e)}")
        return 0  # Return 0 in case of any errors


bot.run("MTE2MjgxMDgwNzczMDU4MTU4NQ.GPkaf7.8oQFQVQercYusZkVLaogdzTR-KwUmsFufN__Wg")
