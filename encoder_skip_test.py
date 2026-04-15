import pandas as pd
import argparse
import sys

def main():
    # 1. Set up the argument parser
    parser = argparse.ArgumentParser(description="Calculate encoder coverage and find missing values from a CSV.")
    parser.add_argument("filename", help="The name of the CSV file to process")
    
    # Parse the arguments from the command line
    args = parser.parse_args()

    # 2. Load the CSV file using the provided filename
    try:
        df = pd.read_csv(args.filename)
    except FileNotFoundError:
        print(f"Error: The file '{args.filename}' was not found.")
        sys.exit(1)
    except Exception as e:
        print(f"Error reading the file: {e}")
        sys.exit(1)

    # Make sure the column exists before trying to access it
    if 'encoder_counts' not in df.columns:
        print("Error: The CSV does not contain an 'encoder_counts' column.")
        sys.exit(1)

    # 3. Extract the 'encoder_counts' column, removing any empty rows
    counts = df['encoder_counts'].dropna()

    # 4. Convert actual counts to a set of integers
    actual_values = set(counts.astype(int))

    # 5. Create a set of expected values from 0 to 5000 (inclusive)
    expected_values = set(range(5001))

    # 6. Find the missing values (Expected minus Actual)
    missing_values = expected_values - actual_values

    # 7. Find the values that are present in the expected range 
    values_present = expected_values.intersection(actual_values)
    unique_count = len(values_present)

    # Calculate the percentage
    percentage = ((unique_count - 1) / 5000) * 100 if unique_count > 0 else 0

    # 8. Print the results
    print(f"Processing file: {args.filename}")
    print("-" * 30)
    print(f"Total unique values in range: {unique_count}")
    print(f"Coverage: {percentage:.2f}%\n")

    # Print the missing values
    missing_values_list = sorted(list(missing_values))
    print(f"There are {len(missing_values_list)} missing values.")
    
    # If there are a lot of missing values, printing them all might flood the terminal.
    # You can print them all, or just print the first few if the list is huge.
    if len(missing_values_list) > 0:
        print(f"Missing values: {missing_values_list}")

if __name__ == "__main__":
    main()