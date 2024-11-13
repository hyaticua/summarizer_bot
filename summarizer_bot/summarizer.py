from openai import AsyncOpenAI
from message import Message


class Summarizer:
    def __init__(self, key: str, model_override: str = None, profile: str = None) -> None:
        self.client = AsyncOpenAI(api_key=key)
        self.model = model_override or "gpt-4o"
        self.base_system_prompt = (
            "You are a helpful tool for summarizing segments of chats in the popular chat service "
            "Discord. You should read the chat transcripts in full and provide a response that is "
            "purely a succinct summary of the input and avoid mentioning any extra information except for "
            "any stylistic changes or roleplaying you are asked to provide. "
        )
        self.profile = profile


    def get_sys_prompt(self, user_metadata) -> str:
        prompt_builder = [self.base_system_prompt]

        if user_metadata:
            prompt_builder.append(f"You are provided some information on the users in the chat. 
                                  Please try to follow their pronouns and respect their given identity if given.")

        if self.profile:
            prompt_builder.append(f"Here are some additional instructions, please follow them as closely as possible: {self.profile} ")
                    
        return "".join(prompt_builder)

    async def summarize(self, msgs: list[Message], user_metadata: list[str]) -> str:
        concat_msgs = "".join(str(msg) for msg in msgs)
        
        concat_metadata = [f"{md["name"]}: {md["info"]}" for md in user_metadata]

        if not concat_msgs:
            return "Sorry, there was nothing to summarize :)"

        response = await self.client.chat.completions.create(
            model=self.model,
            messages=[
                {"role": "system", "content": self.get_sys_prompt(concat_metadata)},
                {"role": "user", "content": concat_msgs},
            ],
        )

        return response.choices[0].message.content
