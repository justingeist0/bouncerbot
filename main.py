import os

import discord
from discord.ext import commands
from PIL import Image
import requests
from io import BytesIO
import imagehash
import random
from pymongo import MongoClient
from difflib import SequenceMatcher

uri = os.getenv("DB")

intents = discord.Intents.all()
intents.members = True  # Enable member updates.
bot = commands.Bot(command_prefix='!', intents=intents)

client = MongoClient(uri)
db = client['serverlessinstance0']
server_collection = db['bouncer']
scammer_collection = db['bouncer-scammers']
cache = {}


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
    if await ignore_user(ctx):
        return
    if target and len(ctx.message.mentions) > 0:
        user = ctx.message.mentions[0]
    else:
        user = ctx.message.author

    user_id = user.id
    guild_id = ctx.guild.id

    if exists(guild_id, user_id):
        await ctx.message.reply(f"Impersonators already being blocked for <@!{user_id}>.")
    else:
        add_user_guild_entry(guild_id, user_id)
        await ctx.send(f"Now blocking impersonators of <@!{user_id}>.")


@bot.command(name="bouncerremove")
async def bouncer_remove(ctx, target: str = None):
    if await ignore_user(ctx):
        return
    if target and len(ctx.message.mentions) > 0:
        user = ctx.message.mentions[0]
    else:
        user = ctx.message.author

    user_id = user.id
    guild_id = ctx.guild.id

    if not exists(guild_id, user_id):
        await ctx.message.reply(f"<@!{user_id}> is not currently in my list.")
    else:
        delete_user_guild_entry(guild_id, user_id)
        await ctx.send(f"Removed <@!{user_id}> from my list.")


@bot.command(name="bouncerlist")
async def list_blockers(ctx):
    if await ignore_user(ctx):
        return
    user_ids = get_user_ids_by_guild_id(ctx.guild.id)
    user_ids = [f"<@!{i}>" for i in user_ids]
    message = "Here's the list of people who can't be impersonated:"
    for user in user_ids:
        message += f'\n- {user}'
    if len(user_ids) == 0:
        message += '\nEmpty. Type !bounceradd @user to add.'
    await ctx.message.reply(message)


@bot.command(name="bouncertestchannel")
async def list_blockers(ctx):
    if await ignore_user(ctx):
        return
    await on_user_kicked(ctx.message.author, ctx.message.author, ctx.guild.id)


@bot.command(name="bouncercheck")
async def scan_server(ctx):
    if await ignore_user(ctx):
        return
    await ctx.message.reply("Starting scan... This may take a while, I'll update you when I'm done.")

    guild = await bot.fetch_guild(ctx.guild.id)

    # Fetch all members
    async for member in guild.fetch_members(limit=None):
        # await ctx.message.reply(f"looking at <@!{member.id}>")
        if not member.bot:
            await kick_if_scammer(guild.id, member.id)

    await ctx.message.reply("Scanning complete.")


@bot.command(name="bouncer")
async def bouncer_list_commands(ctx):
    if await ignore_user(ctx):
        return
    await ctx.message.reply("Okay boss, since you're an admin, here are my commands:\n\n"
                   "!bounceradd @NAME - Prevent non-admin members from sharing this user's profile picture. \n"
                   "!bouncerremove @NAME - Re-allow members to share this user's profile picture. \n\n"
                   "!bouncerlist - List the current users members can't share a profile picture with. \n\n"
                   "!bouncerposthere - Post the updates of the impersonators I kick in the channel this is typed in.\n\n"
                   "!bouncercheck - I'll \"check everyone's IDs\" and kick the people with fake ones. \n\n"
                   "I'm a bot, for further support hit up my creator, <@!1007458938276556870>")


@bot.command(name="bouncerposthere")
async def bouncer_post_here(ctx):
    if await ignore_user(ctx):
        return
    # Retrieve the channel ID from the context
    channel_id = ctx.channel.id

    # Update or set the channel_id in the database
    guild_id = ctx.guild.id  # Assuming you are using guild IDs to differentiate between servers

    get_guild_object(guild_id)

    result = server_collection.update_one(
        {"guild_id": guild_id},
        {"$set": {"channel_id": channel_id}},
        upsert=True  # This creates a new document if one doesn't exist
    )

    delete_guild_from_cache(guild_id)

    # Inform the user
    if result.modified_count > 0 or result.upserted_id is not None:
        await ctx.message.reply("This channel has been set for bouncer posts.")
    else:
        await ctx.send("No update was needed, this channel was already set.")


async def ignore_user(ctx):
    if not ctx.guild:
        return True
    is_admin = any(role.permissions.administrator for role in ctx.author.roles)
    if not is_admin:
        await ctx.message.reply("I only listen to assigned admins. Move along.")
    return not is_admin


async def kick_if_scammer(guild_id, user_id):
    users = get_user_ids_by_guild_id(guild_id)
    print(str(users), "user id", user_id)
    if user_id in users:
        return
    guild = await bot.fetch_guild(guild_id)
    user = await guild.fetch_member(user_id)
    discord_user = await bot.fetch_user(user_id)
    for u in users:
        admin_user = await guild.fetch_member(u)

        #admin_name = admin_user.nick if admin_user.nick is not None else admin_user.name
        #admin_name = str(admin_name).lower()
        # If nickname not equal we are good to continue
        #if not (similar(discord_user.global_name.lower(), admin_name) or similar(user.nick, admin_name)):
        #    continue

        # If avatars are at least 80% similar kick the imposter!
        admin_avatar = admin_user.avatar
        user_avatar = user.avatar

        is_dirty_scammer = image_similarity(admin_avatar, user_avatar) > .8

        if not is_dirty_scammer and discord_user.avatar.url != user_avatar.url:
            is_dirty_scammer = image_similarity(admin_avatar, discord_user.avatar) > .8

        if is_dirty_scammer:
            print("found someone to kick", user.id, user.name)
            # await user.kick()
            save_scammer(user)
            await on_user_kicked(user, admin_user, guild_id)
            break


async def on_user_kicked(user, admin_user, guild_id):
    guild = get_guild_object(guild_id)
    updates_channel = guild['channel_id']
    if updates_channel is not None:
        channel = bot.get_channel(updates_channel)
        if isinstance(channel, discord.TextChannel):
            await channel.send(get_kick_message(user.id))
        else:
            await admin_user.send(f"Kicked someone <@!{user.id}>, but couldn't post update in channel because it's now a voice channel. Type !bouncerposthere in another channel to reroute this there.")
    else:
        await admin_user.send(f"Kicked someone <@!{user.id}>, but the text channel no longer exists. Type !bouncerposthere in another channel to reroute this there.")

    if updates_channel is None:
        await admin_user.send(f"Just kicked someone who was trying to impersonate you <@!{user.id}> in your server. PS type !bouncerposthere in a channel to route these messages over there and I wont DM you then.")

    await user.send(f"You were banned from the server because you look like a scammer. Stop scamming people you heartless fool. Do something else with your life. If you can scam people you have the skills to do so much more. PS. I know where you live keep it up and bad things will happen.")

    try:
        if updates_channel != 1240301259223863408:
            await bot.get_channel(1240301259223863408).send(get_kick_message(user.id) + f" server id: {guild_id}")
        else:
            print('guild is fantasma.dev')
    except Exception as e:
        print(f"Can't post update to fantasma.dev discord server: {e}")


def save_scammer(user):
    scammer_document = {
        "_id": user.id,  # Set the document ID to the user's ID
        "name": user.name,
        "discriminator": user.discriminator,  # This is typically the #1234 part of a Discord username
        "avatar_url": user.avatar.url  # If you want to store the user's avatar URL
    }
    try:
        scammer_collection.insert_one(scammer_document)
        print("User saved successfully.")
    except Exception as e:
        print(f"An error occurred: {e}")


def get_guild_object(guild_id):
    # Check if the guild_id is already in the cache
    if guild_id in cache:
        print("Returning cached guild object.")
        return cache[guild_id]

    # If not in cache, fetch from MongoDB
    guild_document = server_collection.find_one({"guild_id": guild_id})

    if guild_document:
        print("Guild found in database, adding to cache.")
        # Add to cache
        cache[guild_id] = guild_document
        return guild_document
    else:
        new_server = { 'guild_id': guild_id, 'users': [], 'whitelist': [], 'is_premium': False, 'expiration': None, 'channel_id': None }
        server_collection.insert_one(new_server)
        print("No guild found with the specified ID. Created new one.")
        return new_server


def delete_guild_from_cache(guild_id):
    # Remove the guild object from the cache if it exists
    if guild_id in cache:
        print("Guild removed from cache.")
        del cache[guild_id]
    else:
        print("Guild ID not found in cache.")


def get_kick_message(user_id):
    # Define the parts of the message
    prefixes = [
        f"Attention everyone! ",
        f"Just a heads-up! ",
        f"Alert! ",
        f"Guess what? ",
        f"News flash! ",
        f"BOOM! ",
        f"BAM! ",
    ]
    main_messages = [
        f"<@!{user_id}> has been kicked for scamming.",
        f"<@!{user_id}> tried to be a sneaky scammer, but no chance!",
        f"<@!{user_id}> was impersonating the top Gs here. Not on my watch!",
        f"<@!{user_id}> thought they could scam members. Think again!",
        f"<@!{user_id}> got booted for being a scammer.",
        f"<@!{user_id}> is gone and was a dirty scummy scammer. May they not be remembered.",
    ]
    suffixes = [
        " Staying vigilant, folks.",
        " I'll keep an eye out for any more sneaky snakes.",
        " Keeping us safe, team.",
        " Good riddance, I say!",
        " Goodbye to this sewage dwelling gremlin.",
        " Adios sir sneaky sneakerson.",
        " That scammer doesn't shower.",
        " Bye bye you stinky smelly scammer.",
    ]

    # Select one part from each list at random
    prefix = random.choice(prefixes)
    main_message = random.choice(main_messages)
    suffix = random.choice(suffixes)

    # Combine the parts into a final message
    return f"{prefix}{main_message}{suffix}"


def get_user_ids_by_guild_id(guild_id):
    guild = get_guild_object(guild_id)
    if 'users' in guild:
        return guild['users']
    else:
        return []


def delete_user_guild_entry(guild_id, user_id):
    result = server_collection.update_one(
        {"guild_id": guild_id},
        {"$pull": {"users": user_id}}
    )
    if result.modified_count > 0:
        delete_guild_from_cache(guild_id)


def exists(guild_id, user_id):
    users = get_user_ids_by_guild_id(guild_id)
    return user_id in users


def add_user_guild_entry(guild_id, user_id):
    result = server_collection.update_one(
        {"guild_id": guild_id},
        {"$addToSet": {"users": user_id}}
    )
    if result.modified_count > 0:
        delete_guild_from_cache(guild_id)


def similar(a, b):
    return SequenceMatcher(None, a.lower(), b.lower()).ratio() >= 0.5


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


bot.run(os.getenv("DISCORD"))
