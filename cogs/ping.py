from discord.ext import commands

class Utility(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @commands.hybrid_command(name="ping", description="Antwortet mit Pong!")
    async def ping(self, ctx: commands.Context):
        # hybrid_command kümmert sich um Message vs. Interaction
        await ctx.reply("Pong!")

async def setup(bot: commands.Bot):
    await bot.add_cog(Utility(bot))
