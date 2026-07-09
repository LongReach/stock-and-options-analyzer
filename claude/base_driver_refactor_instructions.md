# Instructions for Claude

## Brief Description of Goals

In the folder `D:\CodingProjects\Python\TWS2025\core\ib` is found the class, `IBDriver`. This is a driver specifically for communicating with Interactive Brokers. However, I want to eventually have similar drivers for communicating with other brokerages, such as Charles Schwab.

For now, though, I'd like to have IBDriver derive from an abstract base class called `BaseDriver`. This is step one in a bigger project, in which other drivers, like `SchwabDriver`, will come to exist, also inheriting from `BaseDriver`.

## More Detailed Instructions

1. Make a `BaseDriver` class in its own file in `D:\CodingProjects\Python\TWS2025\core`. It will provide abstract versions of all the public functions in `IBDriver`.
2. Make `IBDriver` be a child class of `BaseDriver`.
3. `IBDriver` should have a static factory method that produces an instance of `IBDriver`, returned as a `BaseDriver` reference.
4. Outside of `core\ib\`, all uses of `IBDriver` should be replaced with `BaseDriver` wherever possible, so that this code will be agnostic to what broker is being used. Please rename variables like `ib_driver` to `base_driver`.