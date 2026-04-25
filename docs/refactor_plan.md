# TamanduCLI Project Plan

## Overview

TamanduCLI is a tool to control and debug robots via Bluetooth Low Energy (BLE) in a flexible and extensible way.

The main goal is to provide a easy way to create extensions, scripts, and commands to other users that not created this project by hiding BLE connection details, message parsing, and other frequently used features that requires a lot of code behind functions in an API.

The idea is to work like a mod system for a game, where you can create modules that can be loaded and used by the main application.

## Project Structure

- `input/`: Input files for the main application and commands.
- `output/`: Output files for the main application and commands.
- `src/`: Source code for the main application and commands.
- `src/commands/`: Where new commands should be created (for new users that want to create their own commands).
- `src/api/`: The API/Library. The toolkit for the users to create their own commands (internal code, users should only consume the API not edit it).

## Message Protocol

A command is a string that contains the command name and its arguments. Arguments are between parentheses and separated by commas.
One message may contain multiple commands separated by ";" (semicolon). A message is a chunk of commands.
When sending a list of data, it may be done in multiple messages.

### Message format:

Max message size is 256 bytes.
```cpp
#define CONFIG_NORDIC_UART_MAX_LINE_LENGTH 256
```

#### Single commands

- Format: command name, 1 character to indicate it is a single(s), 1 character for request(r) or response(s), N arguments separated by commas.
- Example: 'help(s,r);'

#### List commands

First command of the message is a header command.
- Format: command name, 1 character to indicate it is a header(h), 1 character for request(r) or response(s), list size.
- Example: 'help(h,s,1234);'

After the header command, the actual executable commands are sent.
- Format: command name, 1 character to indicate it is a body(b), 1 character for request(r) or response(s), index of the list, N arguments separated by commas.
- Example: 'help(b,s,0,"param_list","_","return all parameters");help(b,s,1,"param_set","name,value","set a parameter value");'

## CLI API Design

Collection of libraries, functions and classes to help the user to create their own commands.

- Commands are handled by a dictionary of commands.
- Use a decorator `@cli_command("command_name")` to register a command in the dictionary.
- To handle commands coming from the device instead of the CLI, use a decorator `@incoming_handler("command_name")`.
- A message parser function that receives a message string and returns a list of commands.
- A command parser that receives a command string and an array of argument names in order and returns a command object (name, type (single or list[header or body]), request or response, index of the list, arguments).
- Responses (like other received messages) should be queued to be processed by the incoming command handler. When a script is waiting for a specific response, it should be processed by the script even if not in the front, and other received messages wait in the queue to be processed.
- Object structure to represent a command: name, type (single or list[header or body]), request or response, index of the list, arguments. On code, all commands should be represented by this object and the pure string representation should be used only for sending commands to the device.
  - {name: str, type: str, isResponse: bool, index: int, arguments: list[str]}
- A function that sends a command to the device. Returns a boolean indicating if the command was sent successfully.
- A function that waits for a response of a specific command from the device and returns a response object (name, type (single or list[header or body]), request or response, index of the list, arguments). Supports timeout.
- A function that sends a command to the device and waits for a response of a specific command from the device and returns a response object (name, type (single or list[header or body]), request or response, index of the list, arguments). Supports timeout.
- A function that receives a list of command objects, the expected response command name, and sends them to the device, handling the batching of the list. Waits for the confirmation resopnse, and returns a boolean indicating if the commands were sent successfully.
  - How the batching works:
  - The commands list should be split into messages of maximum 256 bytes each.
  - The messages should be sent in the order of the list.
  - The messages should be sent with a configurable delay between them to avoid flooding the device. Or wait for a response confirmation from the device to send the next message.
  - Example of message sending: "map_add(h,r,2);map_add(b,r,0,value);map_add(b,r,1,value);"
  - Confirmation response format: command_name(single_character, response_character, arg_)
  - Example of response confirmation: "map_add(s,s,"OK");"

## Refactor Plan

Write the API functions and all internal code at `src/api/` and refactor the command handlers at `src/commands/` to use the API.

Use the Python Prompt Toolkit https://github.com/prompt-toolkit/python-prompt-toolkit to create the CLI and handle the user input and output.

User flow:
- Execute the main application: `uv run src/main.py`
- The application will scan for Bluetooth Low Energy (BLE) devices and list them.
- The user will select a device from the list (single selection list).
- The application will connect to the device and start the CLI.
- The user can type know commands or send raw text to the device.
- The interface will show autocomplete suggestions for the known commands and arguments.
- If the command is unknown, the user will be asked if they want to send the command as raw text to the device.
- If the user wants to send the command as raw text, the application will send the command to the device.
- The application will show the response from the device.
- The user can type 'quit', 'exit', or 'close' to disconnect from the device and close the application.
- The application will disconnect from the device and close the connection.
- If the device is disconnected, the application will exit with an error message.

- For each command, create a handler function that will be called when the command is executed.
- The handler function will be called with the command object and the device object.
- The handler function will execute a script that often will use the API functions to send commands to the device and wait for responses.
- The script will be executed in a separate thread and the main thread will be blocked until the script is finished.
- The script shall use Python Prompt Toolkit to handle the user interactive input and output.